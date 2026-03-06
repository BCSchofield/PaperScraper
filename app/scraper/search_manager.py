"""
Parallel search orchestrator across all configured academic sources.

Each source runs in its own thread (max `max_workers` concurrently).
Results are standardised to a common dict schema and deduplicated by DOI.

Phrase search is supported:
  - arXiv: uses all:"phrase" field syntax via the Atom API
  - PubMed: uses "phrase"[tiab] field tag via NCBI E-utilities
  - bioRxiv/medRxiv: Europe PMC API passes queries through (Lucene syntax, quotes work)
  - ChemRxiv: Figshare API simple text search (phrases stripped of quotes)
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import requests

from app.config import SOURCES, DEFAULT_MAX_RESULTS, DEFAULT_THREAD_WORKERS

logger = logging.getLogger(__name__)

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


# ── Query helpers ─────────────────────────────────────────────────────────────

def _to_arxiv_query(query: str) -> str:
    """Convert our query string to arXiv all: field prefix format.

    "effervescent atomisation" AND "spray"
        → all:"effervescent atomisation" AND all:"spray"
    """
    def _prefix(m):
        term = m.group(0)
        if term.upper() in ("AND", "OR", "ANDNOT"):
            return term
        return f"all:{term}"
    return re.sub(r'"[^"]*"|\b\w+\b', _prefix, query)


def _to_pubmed_query(query: str) -> str:
    """Add [tiab] field tag to quoted phrases for PubMed title/abstract search."""
    return re.sub(r'"([^"]+)"', r'"\1"[tiab]', query)


def _to_chemrxiv_term(query: str) -> str:
    """Strip quotes and boolean operators for ChemRxiv simple text search."""
    result = re.sub(r'"([^"]+)"', r'\1', query)
    result = re.sub(r'\bAND\b|\bOR\b|\bANDNOT\b', ' ', result, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', result).strip()


# ── Per-source search functions ──────────────────────────────────────────────

def _search_arxiv(query: str, max_results: int) -> list[dict]:
    """Search arXiv using the Atom API with proper phrase search support."""
    arxiv_query = _to_arxiv_query(query)
    url = "https://export.arxiv.org/api/query"
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    all_papers: list[dict] = []
    start = 0
    batch = 100

    while len(all_papers) < max_results:
        fetch = min(batch, max_results - len(all_papers))
        params = {"search_query": arxiv_query, "start": start, "max_results": fetch}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        entries = root.findall("atom:entry", ns)
        if not entries:
            break

        for entry in entries:
            arxiv_id = entry.findtext("atom:id", "", ns) or ""
            doi = (entry.findtext("arxiv:doi", "", ns) or "").strip()
            title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n ", " ")
            abstract = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
            authors = ", ".join(
                (a.findtext("atom:name", "", ns) or "")
                for a in entry.findall("atom:author", ns)
            )
            all_papers.append({
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "doi": doi,
                "url": arxiv_id,
                "source": SOURCES["arxiv"],
            })

        if len(entries) < fetch:
            break
        start += len(entries)
        time.sleep(3)  # arXiv rate limit

    return all_papers[:max_results]


def _search_pubmed(query: str, max_results: int) -> list[dict]:
    """Search PubMed via NCBI E-utilities with phrase search support."""
    pm_query = _to_pubmed_query(query)

    # Step 1: esearch to get count and use history server
    search_params = {
        "db": "pubmed",
        "term": pm_query,
        "retmax": min(max_results, 10000),
        "usehistory": "y",
        "retmode": "json",
    }
    resp = requests.get(NCBI_BASE + "esearch.fcgi", params=search_params, timeout=30)
    resp.raise_for_status()
    esearch = resp.json().get("esearchresult", {})
    total = int(esearch.get("count", 0))
    webenv = esearch.get("webenv", "")
    query_key = esearch.get("querykey", "")

    if not total or not webenv:
        return []

    # Step 2: efetch in batches
    papers: list[dict] = []
    retstart = 0
    batch = 100

    while len(papers) < min(total, max_results):
        fetch_params = {
            "db": "pubmed",
            "query_key": query_key,
            "WebEnv": webenv,
            "retstart": retstart,
            "retmax": min(batch, max_results - len(papers)),
            "rettype": "xml",
            "retmode": "xml",
        }
        resp = requests.get(NCBI_BASE + "efetch.fcgi", params=fetch_params, timeout=60)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        articles = root.findall(".//PubmedArticle")
        if not articles:
            break

        for article in articles:
            mc = article.find("MedlineCitation")
            if mc is None:
                continue
            art = mc.find("Article")
            if art is None:
                continue

            title = (art.findtext("ArticleTitle", "") or "").strip()
            abstract_parts = art.findall(".//AbstractText")
            abstract = " ".join((t.text or "") for t in abstract_parts).strip()

            authors = []
            for a in art.findall(".//Author"):
                ln = a.findtext("LastName", "")
                fn = a.findtext("ForeName", "")
                name = f"{fn} {ln}".strip() if fn else ln
                if name:
                    authors.append(name)

            pmid = (mc.findtext("PMID", "") or "").strip()
            doi = ""
            for id_el in article.findall(".//ArticleId"):
                if id_el.get("IdType") == "doi":
                    doi = (id_el.text or "").strip()
                    break

            papers.append({
                "title": title,
                "authors": ", ".join(authors),
                "abstract": abstract,
                "doi": doi,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                "source": SOURCES["pubmed"],
            })

        if len(articles) < batch:
            break
        retstart += len(articles)
        time.sleep(0.34)  # NCBI rate limit ~3 req/sec

    return papers[:max_results]


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
    Quoted phrases in the query are passed through (Lucene syntax supported).
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
    term = _to_chemrxiv_term(query)
    raw_hits: list[dict] = []
    skip = 0
    while len(raw_hits) < max_results:
        batch = min(50, max_results - len(raw_hits))
        params = {"term": term, "limit": batch, "skip": skip}
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
        item = hit.get("item", hit)
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
