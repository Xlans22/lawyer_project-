"""
Lawyer Agent — Phase 1 CLI Prototype
====================================

A fully local, privacy-first pipeline for Egyptian law firms:

  scanned dossier (PDF/image)
        -> Arabic OCR (Tesseract 'ara')
        -> chunking
        -> local embeddings + vector store (ChromaDB)
        -> retrieval-augmented Q&A with a local LLM (Ollama)

No document text ever leaves the machine. All models run locally via Ollama.

Usage
-----
  python lawyer_agent_prototype.py doctor                  # check prerequisites
  python lawyer_agent_prototype.py ingest ./data/case_file.pdf
  python lawyer_agent_prototype.py ingest ./data/          # a whole folder
  python lawyer_agent_prototype.py chat                    # interactive Q&A
  python lawyer_agent_prototype.py reset                   # wipe the vector store

See requirements.txt and README.md for setup.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Configuration — all local, nothing phones home.
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"                 # drop scanned dossiers here
VECTOR_DIR = BASE_DIR / "vectorstore"        # persisted ChromaDB
COLLECTION_NAME = "case_files"

# Ollama model names. Pull them first: `ollama pull <name>`.
LLM_MODEL = os.getenv("LAWYER_LLM_MODEL", "qwen2.5:7b-instruct")
EMBED_MODEL = os.getenv("LAWYER_EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Tesseract: on Windows set the path if it is not on PATH.
TESSERACT_CMD = os.getenv("TESSERACT_CMD")   # e.g. C:\Program Files\Tesseract-OCR\tesseract.exe
OCR_LANG = os.getenv("LAWYER_OCR_LANG", "ara+eng")   # Arabic + Latin fallback
OCR_DPI = int(os.getenv("LAWYER_OCR_DPI", "300"))    # 300 dpi is a good OCR floor

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
SUPPORTED_DOC = {".pdf"}

# ---------------------------------------------------------------------------
# 1. Arabic OCR
# ---------------------------------------------------------------------------

def _configure_tesseract() -> None:
    """Point pytesseract at the binary and verify Arabic data is present."""
    import pytesseract

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    try:
        langs = pytesseract.get_languages(config="")
    except Exception as exc:  # tesseract not found / not on PATH
        raise RuntimeError(
            "Tesseract is not reachable. Install it and either add it to PATH "
            "or set the TESSERACT_CMD env var. See README.md."
        ) from exc

    if "ara" not in langs:
        raise RuntimeError(
            "Tesseract is installed but the Arabic language pack ('ara') is "
            "missing. Install 'tesseract-ocr-ara' (Linux) or the Arabic data "
            "file (Windows). See README.md."
        )


def ocr_image(image) -> str:
    """OCR a single PIL image in Arabic."""
    import pytesseract

    # --psm 1: automatic page segmentation with orientation detection,
    # which handles rotated scans and mixed RTL/LTR layouts reasonably.
    return pytesseract.image_to_string(image, lang=OCR_LANG, config="--psm 1")


def extract_text(path: Path) -> str:
    """Extract text from a PDF or image file, page by page, via OCR.

    For PDFs we first try the embedded text layer (born-digital PDFs). If a
    page has little or no text we fall back to rasterizing and OCR-ing it,
    which is the common case for scanned dossiers.
    """
    _configure_tesseract()
    suffix = path.suffix.lower()

    if suffix in SUPPORTED_IMAGE:
        from PIL import Image

        return ocr_image(Image.open(path))

    if suffix in SUPPORTED_DOC:
        return _extract_pdf(path)

    raise ValueError(f"Unsupported file type: {suffix}")

def _extract_pdf(path: Path) -> str:
    """Read a PDF: use the text layer where present, OCR the rest."""
    from pypdf import PdfReader
    from pdf2image import convert_from_path

    reader = PdfReader(str(path))
    pages_out: list[str] = []

    for i, page in enumerate(reader.pages):
        embedded = (page.extract_text() or "").strip()
        if len(embedded) > 40:  # heuristic: real text layer, skip OCR
            pages_out.append(embedded)
            continue

        # Scanned page: rasterize just this page and OCR it.
        images = convert_from_path(
            str(path), dpi=OCR_DPI, first_page=i + 1, last_page=i + 1
        )
        page_text = "\n".join(ocr_image(img) for img in images)
        pages_out.append(page_text)

    return "\n\n".join(pages_out)

# ---------------------------------------------------------------------------
# 2. Chunking + vector store (ChromaDB, persisted to disk)
# ---------------------------------------------------------------------------

def _get_embeddings():
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(model=EMBED_MODEL, base_url=OLLAMA_BASE_URL)


def _get_vectorstore():
    """Open (or create) the persistent Chroma collection."""
    from langchain_chroma import Chroma

    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=_get_embeddings(),
        persist_directory=str(VECTOR_DIR),
    )


def _iter_files(target: Path) -> Iterable[Path]:
    """Yield every ingestible file under a path (or the file itself)."""
    if target.is_file():
        yield target
        return
    for p in sorted(target.rglob("*")):
        if p.suffix.lower() in SUPPORTED_IMAGE | SUPPORTED_DOC:
            yield p


def ingest(target: Path) -> None:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_core.documents import Document

    files = list(_iter_files(target))
    if not files:
        print(f"No PDFs or images found under: {target}")
        return

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        # Split on Arabic and Latin sentence/paragraph boundaries.
        separators=["\n\n", "\n", "۔", ".", "،", ",", " ", ""],
    )

    all_chunks: list[Document] = []
    for f in files:
        print(f"  OCR + parsing: {f.name} ...")
        text = extract_text(f).strip()
        if not text:
            print(f"    (no text extracted -- skipping {f.name})")
            continue
        chunks = splitter.split_text(text)
        for idx, chunk in enumerate(chunks):
            all_chunks.append(
                Document(
                    page_content=chunk,
                    metadata={"source": f.name, "chunk": idx},
                )
            )
        print(f"    -> {len(chunks)} chunks")

    if not all_chunks:
        print("Nothing to index.")
        return

    print(f"Embedding {len(all_chunks)} chunks with '{EMBED_MODEL}' ...")
    store = _get_vectorstore()
    store.add_documents(all_chunks)
    print(f"Done. Vector store persisted at: {VECTOR_DIR}")

# ---------------------------------------------------------------------------
# 3. Local LLM + retrieval chain (Ollama via LangChain)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "أنت مساعد قانوني دقيق يعمل داخل مكتب محاماة مصري. "
    "أجب فقط بالاعتماد على السياق المستخرج من ملف القضية. "
    "إذا لم تجد الإجابة في السياق، قل بوضوح إن المعلومة غير متوفرة في المستندات. "
    "لا تخترع وقائع أو نصوصًا قانونية. أجب باللغة العربية."
)
# English gloss of the above: "You are a precise legal assistant in an Egyptian
# law firm. Answer only from the retrieved case-file context. If the answer is
# not in the context, say so clearly. Do not invent facts or legal text. Answer
# in Arabic."

PROMPT_TEMPLATE = (
    SYSTEM_PROMPT
    + "\n\n=== السياق (context) ===\n{context}\n\n"
    + "=== السؤال (question) ===\n{question}\n\n"
    + "=== الإجابة ===\n"
)


def _format_docs(docs) -> str:
    blocks = []
    for d in docs:
        src = d.metadata.get("source", "?")
        blocks.append(f"[{src}]\n{d.page_content}")
    return "\n\n---\n\n".join(blocks)


def build_chain():
    from langchain_ollama import ChatOllama
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.runnables import RunnableParallel, RunnablePassthrough

    store = _get_vectorstore()
    retriever = store.as_retriever(search_kwargs={"k": 4})

    llm = ChatOllama(
        model=LLM_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.0,  # legal work: favour faithfulness over creativity
    )
    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)

    # Return sources alongside the answer so the lawyer can verify.
    chain = RunnableParallel(
        {"docs": retriever, "question": RunnablePassthrough()}
    ) | {
        "answer": (
            {
                "context": lambda x: _format_docs(x["docs"]),
                "question": lambda x: x["question"],
            }
            | prompt
            | llm
            | StrOutputParser()
        ),
        "sources": lambda x: x["docs"],
    }
    return chain

# ---------------------------------------------------------------------------
# 4. Interactive CLI
# ---------------------------------------------------------------------------

def chat() -> None:
    if not VECTOR_DIR.exists():
        print("No vector store found. Run 'ingest' on a dossier first.")
        return

    print("=" * 60)
    print("  Lawyer Agent (local) — Arabic case-file Q&A")
    print(f"  LLM: {LLM_MODEL}  |  Embeddings: {EMBED_MODEL}")
    print("  Type your question in Arabic or English. 'exit' to quit.")
    print("=" * 60)

    chain = build_chain()

    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", "q"}:
            break

        try:
            result = chain.invoke(question)
        except Exception as exc:
            print(f"[error] {exc}")
            continue

        print("\n" + result["answer"].strip())
        sources = sorted({d.metadata.get("source", "?") for d in result["sources"]})
        if sources:
            print("\n-- sources: " + ", ".join(sources))


def doctor() -> None:
    """Check every external prerequisite and report what's missing.

    Runs all checks (does not stop at the first failure) so you see the full
    picture in one pass. Exit code is non-zero if anything is wrong.
    """
    import importlib.util
    import shutil
    import urllib.request

    ok = "[OK]"
    bad = "[X] "
    warn = "[!] "
    problems = 0

    print("Lawyer Agent - environment check\n" + "-" * 40)

    # 1. Python packages
    pkgs = [
        "pytesseract", "PIL", "pdf2image", "pypdf",
        "langchain_ollama", "langchain_chroma", "langchain_text_splitters",
        "chromadb",
    ]
    missing = [p for p in pkgs if importlib.util.find_spec(p) is None]
    if missing:
        problems += 1
        print(f"{bad} Python packages missing: {', '.join(missing)}")
        print("     fix: pip install -r requirements.txt")
    else:
        print(f"{ok} Python packages installed")

    # 2. Tesseract binary + Arabic pack
    try:
        import pytesseract
        if TESSERACT_CMD:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        ver = pytesseract.get_tesseract_version()
        langs = pytesseract.get_languages(config="")
        if "ara" in langs:
            print(f"{ok} Tesseract {ver} with Arabic pack ('ara')")
        else:
            problems += 1
            print(f"{bad} Tesseract {ver} found, but 'ara' language pack missing")
            print("     fix: reinstall and tick 'Arabic', or add ara.traineddata")
    except Exception as exc:
        problems += 1
        print(f"{bad} Tesseract not reachable: {exc}")
        print("     fix: install it, then set TESSERACT_CMD or add it to PATH")

    # 3. Poppler (pdftoppm / pdftocairo) — needed by pdf2image for scanned PDFs
    poppler = shutil.which("pdftoppm") or shutil.which("pdftocairo")
    if poppler:
        print(f"{ok} Poppler found: {poppler}")
    else:
        problems += 1
        print(f"{bad} Poppler not on PATH (pdf2image needs it for scanned PDFs)")
        print("     fix: install Poppler and add its Library\\bin folder to PATH")

    # 4. Ollama server + required models
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=5) as r:
            import json
            data = json.load(r)
        installed = {m.get("name", "").split(":")[0] for m in data.get("models", [])}
        installed |= {m.get("name", "") for m in data.get("models", [])}
        print(f"{ok} Ollama server reachable at {OLLAMA_BASE_URL}")
        for model, label in [(LLM_MODEL, "LLM"), (EMBED_MODEL, "embeddings")]:
            base = model.split(":")[0]
            if model in installed or base in installed:
                print(f"   {ok} {label} model '{model}' present")
            else:
                problems += 1
                print(f"   {bad} {label} model '{model}' not pulled")
                print(f"        fix: ollama pull {model}")
    except Exception as exc:
        problems += 1
        print(f"{bad} Ollama not reachable at {OLLAMA_BASE_URL}: {exc}")
        print("     fix: install Ollama and make sure it is running")

    print("-" * 40)
    if problems:
        print(f"{warn}{problems} problem(s) found. See fixes above.")
        sys.exit(1)
    print(f"{ok} All good - you're ready to ingest and chat.")


def reset() -> None:
    import shutil

    if VECTOR_DIR.exists():
        shutil.rmtree(VECTOR_DIR)
        print(f"Removed vector store: {VECTOR_DIR}")
    else:
        print("Nothing to reset.")


def _force_utf8_console() -> None:
    """Windows consoles default to cp1252, which cannot encode Arabic text.

    Without this, printing an Arabic answer raises UnicodeEncodeError. We
    reconfigure stdout/stderr to UTF-8 and ask the Windows console for UTF-8
    code page 65001 so the glyphs actually render.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # not a TextIOWrapper (e.g. piped) — nothing to do
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass


def main() -> None:
    _force_utf8_console()

    parser = argparse.ArgumentParser(
        description="Local Arabic legal-document RAG prototype."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="OCR + index a file or folder")
    p_ingest.add_argument("path", type=Path, help="PDF/image file or a folder")

    sub.add_parser("chat", help="interactive Q&A over indexed dossiers")
    sub.add_parser("doctor", help="check that all prerequisites are installed")
    sub.add_parser("reset", help="delete the local vector store")

    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)

    if args.command == "ingest":
        ingest(args.path)
    elif args.command == "chat":
        chat()
    elif args.command == "doctor":
        doctor()
    elif args.command == "reset":
        reset()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        # Friendly message for the common setup problems (missing Tesseract, etc.)
        print(f"\n[setup error] {exc}", file=sys.stderr)
        sys.exit(1)

