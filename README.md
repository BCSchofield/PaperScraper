# PaperScraper

A cross-platform desktop application for searching academic literature, exporting results to Excel, and running sentiment analysis on abstracts.

## Features

- Search **arXiv, PubMed, bioRxiv, medRxiv, ChemRxiv** in parallel
- Configurable result limits and thread-pool size
- Automatic deduplication across sources (with merged source labels)
- One Excel sheet per search term — columns: Title, Authors, Abstract, DOI, Link, Source(s)
- **Sentiment Analysis tab** — score paper abstracts positive/negative/neutral against any topic using HuggingFace Inference API
- Remembers past search terms, output folder, and settings
- Modern dark theme (Apple-inspired)

---

## Running from source (development)

### 1. Install Python 3.11+

Download from [python.org](https://www.python.org/downloads/).

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the app

```bash
python main.py
```

---

## Building a standalone executable (no Python required)

The built executable works on **Windows** (.exe) and **macOS** (.app) without any Python installation.

### 1. Install build tools

```bash
pip install pyinstaller
pip install -r requirements.txt
```

### 2. Build

```bash
pyinstaller PaperScraper.spec
```

The output will be in `dist/`:
- **macOS**: `dist/PaperScraper.app` — drag to Applications
- **Windows**: `dist/PaperScraper.exe` — run directly or create a shortcut

> **Note:** Build on each target OS separately. A macOS build won't run on Windows and vice versa.

---

## Sentiment Analysis

The app uses the **HuggingFace Inference API** (free tier) with `facebook/bart-large-mnli` for zero-shot classification.

- **No account required** for basic use — the model is public and free to call without a token
- Without a token, requests are rate-limited by HuggingFace (you may see short delays between papers)
- The analysis scores each paper's abstract as **Positive / Negative / Neutral** with respect to your chosen topic

### Optional: HuggingFace API Token (higher rate limits)

1. Create a free account at [huggingface.co](https://huggingface.co/join)
2. Go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
3. Click **New token** → choose **Read** access → copy the token (starts with `hf_`)
4. Paste it into the **HuggingFace API Token** field in the Sentiment Analysis tab

> The token is **not saved to disk** — you will need to re-enter it each session. This is intentional for security.

---

## Project Structure

```
PaperScraper/
├── main.py                     Entry point
├── requirements.txt
├── PaperScraper.spec           PyInstaller build config
└── app/
    ├── config.py               Colours, constants, source list
    ├── gui/
    │   ├── main_window.py      Root CTk window + tab view
    │   ├── search_tab.py       Search UI
    │   └── sentiment_tab.py    Sentiment Analysis UI
    ├── scraper/
    │   └── search_manager.py   Parallel search orchestrator
    ├── analysis/
    │   └── sentiment.py        HuggingFace API integration
    ├── export/
    │   └── excel_exporter.py   openpyxl Excel writer
    └── storage/
        └── history.py          SQLite persistence (history + settings)
```
