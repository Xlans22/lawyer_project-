"""
Central configuration for the Lawyer Agent desktop application.

Everything tunable lives here: filesystem layout, theme tokens, document
categories, and environment overrides. No other module should hard-code paths
or colours.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
CLIENTS_DIR = DATA_DIR / "clients"          # one dossier folder per client
VECTOR_DIR = DATA_DIR / "vectorstore"       # ChromaDB persistence
DB_PATH = DATA_DIR / "lawyer_agent.db"      # SQLite
INBOX_DIR = DATA_DIR / "inbox"              # unassigned mobile uploads


def ensure_dirs() -> None:
    """Create the data tree if missing. Safe to call repeatedly."""
    for d in (DATA_DIR, CLIENTS_DIR, VECTOR_DIR, INBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Document categories
# ---------------------------------------------------------------------------
# Folder names are deliberately ASCII: Arabic folder names cause encoding
# headaches across Windows tools, backup software and zip utilities. The UI
# shows the Arabic label; the disk keeps a safe name.

CATEGORIES: dict[str, str] = {
    "contracts": "عقود",              # عقود
    "ids": "بطاقات",        # بطاقات
    "rulings": "أحكام",           # أحكام
    "correspondence": "مراسلات",  # مراسلات
    "scans": "مستندات",  # مستندات
}
DEFAULT_CATEGORY = "scans"


def category_label(key: str) -> str:
    """Arabic display label for a category key."""
    return CATEGORIES.get(key, key)


def category_from_label(label: str) -> str:
    """Reverse lookup, tolerating unknown input."""
    for key, val in CATEGORIES.items():
        if val == label:
            return key
    return DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# Local AI (Ollama)
# ---------------------------------------------------------------------------

LLM_MODEL = os.getenv("LAWYER_LLM_MODEL", "qwen2.5:7b-instruct")
EMBED_MODEL = os.getenv("LAWYER_EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
COLLECTION_NAME = "case_files"

# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

TESSERACT_CMD = os.getenv("TESSERACT_CMD")
OCR_LANG = os.getenv("LAWYER_OCR_LANG", "ara+eng")
OCR_DPI = int(os.getenv("LAWYER_OCR_DPI", "300"))

SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_DOC = {".pdf"}
SUPPORTED_ALL = SUPPORTED_IMAGE | SUPPORTED_DOC

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# ---------------------------------------------------------------------------
# Mobile bridge
# ---------------------------------------------------------------------------

MOBILE_PORT = int(os.getenv("LAWYER_MOBILE_PORT", "8756"))

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

NOTIFY_LOOKAHEAD_DAYS = int(os.getenv("LAWYER_NOTIFY_DAYS", "3"))
NOTIFY_POLL_SECONDS = int(os.getenv("LAWYER_NOTIFY_POLL", "900"))  # 15 min

# ---------------------------------------------------------------------------
# Theme — "premium legal" palette
# ---------------------------------------------------------------------------
# deep navy (authority) + slate (structure) + parchment (paper warmth),
# with a restrained brass accent. Deliberately not flashy.

THEME = {
    "shell":         "#0E2236",  # dark navy app canvas (behind the cards)
    "navy":          "#0F2740",  # headers, primary buttons
    "navy_light":    "#1B3A5C",  # hover
    "navy_deep":     "#081A2B",  # rail / toolbar / status bar chrome
    "slate":         "#54657A",  # secondary text, borders
    "slate_light":   "#8A99AB",
    "parchment":     "#FBF8F1",  # card + reading surface
    "parchment_alt": "#F1ECE0",  # row hover / alt
    "white":         "#FFFFFF",  # inputs, nested list cards
    "brass":         "#A9853F",  # ACCENT (active/focus)
    "brass_light":   "#C9A961",
    "brass_soft":    "#EFE6D4",  # subtle accent hover fill
    "ink":           "#1C2733",  # primary text
    "muted":         "#6B7A8C",
    "border":        "#DCD5C7",
    "danger":        "#9B2C2C",
    "success":       "#2F6B4F",
}

# Arabic-first font stack. Cairo and IBM Plex Sans Arabic are the targets;
# the rest are widely-installed fallbacks so the app never renders tofu.
FONT_STACK = [
    "Cairo",
    "Tajawal",
    "IBM Plex Sans Arabic",
    "Noto Naskh Arabic",
    "Segoe UI",
    "Tahoma",
]
FONT_MONO = ["Cascadia Mono", "Consolas", "Courier New"]

BASE_FONT_SIZE = 12
TITLE_FONT_SIZE = 20
HEADING_FONT_SIZE = 15

# UI direction. True gives an authentic Arabic RTL application, which mirrors
# the whole layout (navigation rail lands on the right, chat on the left).
# Set to False if you prefer the literal left-rail / right-chat arrangement.
UI_RTL = os.getenv("LAWYER_RTL", "1") not in {"0", "false", "False"}
