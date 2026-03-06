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
_TIMEOUT = 30  # seconds per request
_RETRY_WAIT = 10  # seconds to wait when model is loading


def _build_input(paper: dict, topic: str) -> str:
    title = paper.get("title", "")
    abstract = paper.get("abstract", "")
    # Truncate to keep within model token limits (~1500 chars is safe)
    text = f"Title: {title}. Abstract: {abstract}"[:1500]
    return text


def analyse_paper(
    paper: dict,
    topic: str,
    api_token: Optional[str] = None,
) -> dict:
    """
    Returns paper dict with two extra keys:
      - sentiment: "positive" | "negative" | "neutral"
      - confidence: float 0–1
    """
    url = f"{HF_API_BASE}/{HF_MODEL}"
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"

    payload = {
        "inputs": _build_input(paper, topic),
        "parameters": {
            "candidate_labels": _CANDIDATE_LABELS,
            "hypothesis_template": f"This paper has a {{}} view on {topic}.",
        },
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)

            if resp.status_code == 503:
                # Model is loading
                logger.info("HF model loading, waiting %ss…", _RETRY_WAIT)
                time.sleep(_RETRY_WAIT)
                continue

            if resp.status_code == 429:
                logger.warning("HF rate limit hit, waiting 30s…")
                time.sleep(30)
                continue

            resp.raise_for_status()
            data = resp.json()

            labels = data.get("labels", [])
            scores = data.get("scores", [])

            if labels and scores:
                top_label = labels[0]
                top_score = round(scores[0], 4)
                result = dict(paper)
                result["sentiment"] = top_label
                result["confidence"] = top_score
                return result
            else:
                # Valid 200 but unexpected empty payload — no point retrying
                logger.warning("HF API returned empty labels/scores on attempt %d", attempt + 1)
                break

        except requests.RequestException as exc:
            logger.error("HF API error (attempt %d): %s", attempt + 1, exc)
            time.sleep(2)

    # Fallback on failure
    result = dict(paper)
    result["sentiment"] = "error"
    result["confidence"] = 0.0
    return result


def analyse_papers(
    papers: list[dict],
    topic: str,
    api_token: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> list[dict]:
    """Analyse all papers, calling progress_callback(done, total) after each."""
    total = len(papers)
    results = []
    for i, paper in enumerate(papers):
        results.append(analyse_paper(paper, topic, api_token))
        if progress_callback:
            progress_callback(i + 1, total)
        # Small delay to avoid hammering the free API
        time.sleep(0.5)
    return results
