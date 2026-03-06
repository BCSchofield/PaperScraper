"""App-wide configuration and constants."""

APP_NAME = "PaperScraper"
APP_VERSION = "1.0.0"
WINDOW_SIZE = "1300x820"
WINDOW_MIN_SIZE = (1100, 700)

# Apple-inspired dark colour palette
COLORS = {
    "bg_primary": "#1c1c1e",
    "bg_secondary": "#2c2c2e",
    "bg_tertiary": "#3a3a3c",
    "bg_input": "#3a3a3c",
    "accent": "#0a84ff",
    "accent_hover": "#0071e3",
    "accent_dim": "#0a84ff33",
    "text_primary": "#ffffff",
    "text_secondary": "#ebebf5",
    "text_muted": "#8e8e93",
    "separator": "#38383a",
    "success": "#30d158",
    "warning": "#ff9f0a",
    "error": "#ff453a",
    "tag_bg": "#0a84ff22",
    "tag_border": "#0a84ff",
    "row_hover": "#323234",
}

FONT_FAMILY = "SF Pro Display" if "darwin" in __import__("sys").platform else "Segoe UI"

# Search sources with display labels
SOURCES = {
    "arxiv": "arXiv",
    "pubmed": "PubMed",
    "biorxiv": "bioRxiv",
    "medrxiv": "medRxiv",
    "chemrxiv": "ChemRxiv",
}

DEFAULT_MAX_RESULTS = 50
DEFAULT_THREAD_WORKERS = 3

# HuggingFace zero-shot model used via Inference API
HF_MODEL = "facebook/bart-large-mnli"
HF_API_BASE = "https://api-inference.huggingface.co/models"

EXCEL_COLUMNS = ["Title", "Authors", "Abstract", "DOI", "Link", "Source(s)"]
EXCEL_SENTIMENT_COLUMNS = EXCEL_COLUMNS + ["Sentiment", "Confidence"]
