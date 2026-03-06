"""
Sentiment analysis via HuggingFace Inference API (zero-shot classification).

Model: facebook/bart-large-mnli
For each paper the abstract (+ title) is classified against the user-supplied
topic using three candidate labels: positive, negative, neutral.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import requests

from app.config import HF_MODEL, HF_API_BASE

logger = logging.getLogger(__name__)

_CANDIDATE_LABELS = ["positive", "negative", "neutral"]
_TIMEOUT = 60  # seconds per request
_RETRY_WAIT = 20  # minimum seconds to wait when model is loading


def _build_input(paper: dict, topic: str) -> str:
    title = paper.get("title", "").strip()
    abstract = paper.get("abstract", "").strip()
    # Use whichever parts we have; fall back to just the title if no abstract
    if abstract:
        text = f"Title: {title}. Abstract: {abstract}"
    elif title:
        text = f"Title: {title}."
    else:
        text = ""
    return text[:1500]


def _has_content(paper: dict) -> bool:
    """Return False if there is nothing meaningful to classify."""
    return bool(
        (paper.get("title") or "").strip()
        or (paper.get("abstract") or "").strip()
    )


def analyse_paper(
    paper: dict,
    topic: str,
    api_token: Optional[str] = None,
    candidate_labels: Optional[list] = None,
    hypothesis_template: Optional[str] = None,
) -> dict:
    """
    Returns paper dict with two extra keys:
      - sentiment: label from candidate_labels (default: positive/negative/neutral)
      - confidence: float 0–1

    hypothesis_template: string with a single {} placeholder that HuggingFace fills
      with each candidate label.  Defaults to sentiment framing.
    """
    url = f"{HF_API_BASE}/{HF_MODEL}"
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    labels = candidate_labels or _CANDIDATE_LABELS
    template = hypothesis_template or f"This paper has a {{}} view on {topic}."

    payload = {
        "inputs": _build_input(paper, topic),
        "parameters": {
            "candidate_labels": labels,
            "hypothesis_template": template,
        },
    }

    if not _has_content(paper):
        result = dict(paper)
        result["sentiment"] = "neutral"
        result["confidence"] = 0.0
        result["error_detail"] = "No content to classify"
        return result

    last_error = "unknown error"

    for attempt in range(5):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)

            if resp.status_code == 503:
                wait = _RETRY_WAIT
                try:
                    wait = max(resp.json().get("estimated_time", _RETRY_WAIT), _RETRY_WAIT)
                    wait = min(wait, 120)
                except Exception:
                    pass
                last_error = f"503 model loading (waited {wait}s)"
                logger.info("HF model loading, waiting %ss… (attempt %d/5)", wait, attempt + 1)
                time.sleep(wait)
                continue

            if resp.status_code == 429:
                last_error = "429 rate limit"
                logger.warning("HF rate limit hit, waiting 60s… (attempt %d/5)", attempt + 1)
                time.sleep(60)
                continue

            # Log full response body for any non-2xx so we can diagnose
            if not resp.ok:
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text[:500]
                last_error = f"HTTP {resp.status_code}: {body}"
                logger.error("HF API HTTP %d on attempt %d: %s", resp.status_code, attempt + 1, body)
                # Don't retry definitive client errors (except 429 handled above)
                if 400 <= resp.status_code < 500:
                    break
                time.sleep(3)
                continue

            data = resp.json()
            labels_out = data.get("labels", [])
            scores = data.get("scores", [])

            if labels_out and scores:
                result = dict(paper)
                result["sentiment"] = labels_out[0]
                result["confidence"] = round(scores[0], 4)
                return result
            else:
                last_error = f"Empty response: {data}"
                logger.warning("HF API returned empty labels/scores on attempt %d: %s", attempt + 1, data)
                break

        except requests.RequestException as exc:
            last_error = str(exc)
            logger.error("HF API request error (attempt %d): %s", attempt + 1, exc)
            time.sleep(3)

    # Fallback on failure
    logger.error("HF API failed for paper '%s': %s", paper.get("title", "?")[:60], last_error)
    result = dict(paper)
    result["sentiment"] = "error"
    result["confidence"] = 0.0
    result["error_detail"] = last_error
    return result


def analyse_papers(
    papers: list[dict],
    topic: str,
    api_token: Optional[str] = None,
    candidate_labels: Optional[list] = None,
    hypothesis_template: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> list[dict]:
    """Analyse all papers, calling progress_callback(done, total) after each."""
    total = len(papers)
    results = []
    for i, paper in enumerate(papers):
        results.append(analyse_paper(paper, topic, api_token, candidate_labels, hypothesis_template))
        if progress_callback:
            progress_callback(i + 1, total)
        # Small delay to avoid hammering the free API
        time.sleep(0.5)
    return results
