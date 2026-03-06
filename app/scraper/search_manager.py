"""
Parallel search orchestrator across all configured academic sources.

Each source runs in its own thread (max `max_workers` concurrently).
Results are standardised to a common dict schema and deduplicated by DOI.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from app.config import SOURCES, DEFAULT_MAX_RESULTS, DEFAULT_THREAD_WORKERS

logger = logging.getLogger(__name__)

# ── Standardised paper schema keys ─────────────────────────────────────────
# title, authors (str), abstract, doi, url, source (str)

def _normalise(raw: dict, source_key: str) -> dict:
    """Convert a raw paperscraper dict into our standard schema."""
    authors = raw.get("authors", [])
    if isinstance(authors, list):
        authors = ", ".join(str(a) for a in authors)

    return {
        "title": str(raw.get("title", "") or "").strip(),
        "authors": str(authors).strip(),
        "abstract": str(raw.get("abstract", "") or "").strip(),
        "doi": str(raw.get("doi", "") or "").strip(),
        "url": str(raw.get("url", "") or raw.get("link", "") or "").strip(),
        "source": SOURCES.get(source_key, source_key),
    }


def _read_jsonl(path: str) -> list[dict]:
    papers = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        papers.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        logger.warning("Could not read temp file %s: %s", path, exc)
    return papers


# ── Per-source search functions ──────────────────────────────────────────────

def _search_arxiv(query: str, max_results: int) -> list[dict]:
    from paperscraper.arxiv import get_and_dump_arxiv_papers
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    try:
        get_and_dump_arxiv_papers([[query]], tmp.name, max_results=max_results)
        return [_normalise(p, "arxiv") for p in _read_jsonl(tmp.name)]
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _search_pubmed(query: str, max_results: int) -> list[dict]:
    from paperscraper.pubmed import get_and_dump_pubmed_papers
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    try:
        get_and_dump_pubmed_papers([[query]], tmp.name, max_results=max_results)
        return [_normalise(p, "pubmed") for p in _read_jsonl(tmp.name)]
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _search_xrxiv(server: str, query: str, max_results: int) -> list[dict]:
    """bioRxiv / medRxiv / ChemRxiv via paperscraper's XRXivQuery."""
    from paperscraper.xrxiv.xrxiv_query import XRXivQuery
    scraper = XRXivQuery(server)
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    try:
        scraper.search_keywords(
            keywords=[query],
            output_filepath=tmp.name,
            max_results=max_results,
        )
        return [_normalise(p, server) for p in _read_jsonl(tmp.name)]
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


_SOURCE_FN = {
    "arxiv": _search_arxiv,
    "pubmed": _search_pubmed,
    "biorxiv": lambda q, n: _search_xrxiv("biorxiv", q, n),
    "medrxiv": lambda q, n: _search_xrxiv("medrxiv", q, n),
    "chemrxiv": lambda q, n: _search_xrxiv("chemrxiv", q, n),
}


# ── Deduplication ─────────────────────────────────────────────────────────────

def _deduplicate(papers: list[dict]) -> list[dict]:
    """
    Merge papers with identical DOIs.
    Papers without DOIs are kept as-is (matched by title).
    """
    doi_index: dict[str, dict] = {}
    title_index: dict[str, dict] = {}
    result: list[dict] = []

    for p in papers:
        doi = p["doi"].lower().strip() if p["doi"] else ""
        title_key = p["title"].lower().strip()[:80]

        if doi and doi in doi_index:
            existing = doi_index[doi]
            if p["source"] not in existing["source"]:
                existing["source"] += f", {p['source']}"
            continue

        if not doi and title_key and title_key in title_index:
            existing = title_index[title_key]
            if p["source"] not in existing["source"]:
                existing["source"] += f", {p['source']}"
            continue

        result.append(p)
        if doi:
            doi_index[doi] = p
        if title_key:
            title_index[title_key] = p

    return result


# ── Public API ────────────────────────────────────────────────────────────────

def run_search(
    query: str,
    sources: Optional[list[str]] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_workers: int = DEFAULT_THREAD_WORKERS,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> list[dict]:
    """
    Search `query` across all (or selected) sources in parallel.

    progress_callback(source_key, status) is called from worker threads.
    status is one of: "searching", "done", "error"

    Returns a deduplicated list of paper dicts.
    """
    if sources is None:
        sources = list(SOURCES.keys())

    all_papers: list[dict] = []

    def _run_source(source_key: str) -> tuple[str, list[dict]]:
        if progress_callback:
            progress_callback(source_key, "searching")
        fn = _SOURCE_FN.get(source_key)
        if fn is None:
            if progress_callback:
                progress_callback(source_key, "error")
            return source_key, []
        try:
            papers = fn(query, max_results)
            if progress_callback:
                progress_callback(source_key, "done")
            return source_key, papers
        except Exception as exc:
            logger.error("Source %s failed for query '%s': %s", source_key, query, exc)
            if progress_callback:
                progress_callback(source_key, "error")
            return source_key, []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_source, s): s for s in sources}
        for future in as_completed(futures):
            _, papers = future.result()
            all_papers.extend(papers)

    return _deduplicate(all_papers)
