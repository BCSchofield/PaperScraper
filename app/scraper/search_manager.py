"""
Parallel search orchestrator across all configured academic sources.

Each source runs in its own thread (max `max_workers` concurrently).
Results are standardised to a common dict schema and deduplicated by DOI.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import requests

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


# ── Query parsing ────────────────────────────────────────────────────────────

def _parse_query(query: str) -> list[list[str]]:
    """
    Convert a query string into paperscraper keyword format (list of AND-groups).

    "effervescent AND atomisation"
        → [["effervescent", "atomisation"]]

    "effervescent AND atomisation OR effervescent AND atomization"
        → [["effervescent", "atomisation"], ["effervescent", "atomization"]]

    Plain strings with no operators pass through unchanged:
    "machine learning"  → [["machine learning"]]
    """
    or_groups = re.split(r'\s+OR\s+', query.strip(), flags=re.IGNORECASE)
    return [
        [t.strip() for t in re.split(r'\s+AND\s+', group.strip(), flags=re.IGNORECASE)]
        for group in or_groups
        if group.strip()
    ]


# ── Per-source search functions ──────────────────────────────────────────────

def _search_arxiv(query: str, max_results: int) -> list[dict]:
    from paperscraper.arxiv import get_and_dump_arxiv_papers
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    try:
        get_and_dump_arxiv_papers(_parse_query(query), tmp.name, max_results=max_results)
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
        get_and_dump_pubmed_papers(_parse_query(query), tmp.name, max_results=max_results)
        return [_normalise(p, "pubmed") for p in _read_jsonl(tmp.name)]
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _search_biorxiv(query: str, max_results: int) -> list[dict]:
    """Search bioRxiv via Europe PMC API (no local files required)."""
    return _search_via_europepmc(query, "biorxiv", max_results)


def _search_medrxiv(query: str, max_results: int) -> list[dict]:
    """Search medRxiv via Europe PMC API (no local files required)."""
    return _search_via_europepmc(query, "medrxiv", max_results)


def _search_via_europepmc(query: str, server: str, max_results: int) -> list[dict]:
    """
    Search bioRxiv or medRxiv preprints via Europe PMC REST API.
    Paginates via cursorMark to support up to max_results > 100.
    """
    publisher = {"biorxiv": "bioRxiv", "medrxiv": "medRxiv"}[server]
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    base_q = f'({query}) AND SRC:PPR AND PUBLISHER:"{publisher}"'

    raw_items: list[dict] = []
    cursor = "*"
    while len(raw_items) < max_results:
        batch = min(100, max_results - len(raw_items))
        params = {
            "query": base_q,
            "format": "json",
            "pageSize": batch,
            "resultType": "core",
            "cursorMark": cursor,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        page = (data.get("resultList") or {}).get("result", [])
        if not page:
            break
        raw_items.extend(page)
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    papers = []
    for r in raw_items[:max_results]:
        doi = r.get("doi", "") or ""
        authors = ", ".join(
            a.get("fullName", "")
            for a in (r.get("authorList") or {}).get("author", [])
        )
        papers.append({
            "title": (r.get("title") or "").rstrip("."),
            "authors": authors,
            "abstract": r.get("abstractText", "") or "",
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else "",
            "source": SOURCES.get(server, server),
        })
    return papers


def _search_chemrxiv(query: str, max_results: int) -> list[dict]:
    """Search ChemRxiv via their public Figshare-based API. Paginates in batches of 50."""
    url = "https://chemrxiv.org/engage/chemrxiv/public-api/v1/items"
    raw_hits: list[dict] = []
    skip = 0
    while len(raw_hits) < max_results:
        batch = min(50, max_results - len(raw_hits))
        params = {"term": query, "limit": batch, "skip": skip}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        page = data.get("itemHits", [])
        if not page:
            break
        raw_hits.extend(page)
        skip += len(page)
        if len(page) < batch:
            break

    papers = []
    for hit in raw_hits[:max_results]:
        item = hit.get("item", hit)  # handle both nested {"item": {...}} and flat
        doi = item.get("doi", "") or ""
        authors = ", ".join(
            f"{a.get('firstName', '')} {a.get('lastName', '')}".strip()
            for a in (item.get("authors") or [])
        )
        papers.append({
            "title": str(item.get("title", "") or "").strip(),
            "authors": authors,
            "abstract": str(item.get("abstract", "") or item.get("description", "") or "").strip(),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else item.get("htmlUrl", ""),
            "source": SOURCES.get("chemrxiv", "ChemRxiv"),
        })
    return papers


_SOURCE_FN = {
    "arxiv": _search_arxiv,
    "pubmed": _search_pubmed,
    "biorxiv": _search_biorxiv,
    "medrxiv": _search_medrxiv,
    "chemrxiv": _search_chemrxiv,
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
