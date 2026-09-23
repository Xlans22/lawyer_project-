"""
OCR engine: Tesseract with Arabic support, for PDFs and direct images.

Handles the three intake paths of the desktop app:
  * imported PDFs (text layer where present, OCR for scanned pages)
  * imported images (.jpg/.png/...)
  * photos arriving from the mobile bridge (in-memory bytes)
"""

from __future__ import annotations

import io
from pathlib import Path

from core import config


class OcrUnavailable(RuntimeError):
    """Raised when Tesseract or the Arabic pack is missing."""


def configure_tesseract() -> None:
    """Point pytesseract at the binary and verify the Arabic pack is present."""
    import pytesseract

    if config.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
    try:
        langs = pytesseract.get_languages(config="")
    except Exception as exc:
        raise OcrUnavailable(
            "Tesseract is not reachable. Install it and either add it to PATH "
            "or set the TESSERACT_CMD environment variable."
        ) from exc

    if "ara" not in langs:
        raise OcrUnavailable(
            "Tesseract is installed but the Arabic language pack ('ara') is "
            "missing. Re-run the installer and tick 'Arabic' under additional "
            "language data."
        )


def ocr_image(image) -> str:
    """OCR a single PIL image, Arabic-first with Latin fallback."""
    import pytesseract

    # --psm 1: automatic page segmentation with orientation detection, which
    # copes with rotated phone photos and mixed RTL/LTR layouts.
    return pytesseract.image_to_string(
        image, lang=config.OCR_LANG, config="--psm 1"
    )


def extract_from_image_path(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as img:
        return ocr_image(img)


def extract_from_bytes(payload: bytes) -> str:
    """OCR an in-memory image (mobile upload)."""
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as img:
        return ocr_image(img)


def extract_from_pdf(path: Path) -> str:
    """Read a PDF: use the text layer where present, OCR the rest."""
    from pypdf import PdfReader
    from pdf2image import convert_from_path

    reader = PdfReader(str(path))
    pages: list[str] = []

    for i, page in enumerate(reader.pages):
        embedded = (page.extract_text() or "").strip()
        if len(embedded) > 40:
            pages.append(embedded)
            continue
        images = convert_from_path(
            str(path), dpi=config.OCR_DPI, first_page=i + 1, last_page=i + 1
        )
        pages.append("\n".join(ocr_image(img) for img in images))

    return "\n\n".join(pages)


def extract_text(path: Path) -> str:
    """Dispatch on file type. Raises ValueError for unsupported formats."""
    configure_tesseract()
    suffix = path.suffix.lower()
    if suffix in config.SUPPORTED_IMAGE:
        return extract_from_image_path(path)
    if suffix in config.SUPPORTED_DOC:
        return extract_from_pdf(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def probe() -> tuple[bool, str]:
    """Non-raising health check for the Settings screen. -> (healthy, message)"""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False, "pytesseract is not installed."

    try:
        configure_tesseract()
    except OcrUnavailable as exc:
        return False, str(exc)

    import pytesseract

    version = pytesseract.get_tesseract_version()
    return True, f"Tesseract {version} with '{config.OCR_LANG}' available."
