"""
Retrieval-augmented generation over the local vector store.

Critical design point for legal work: retrieval is **scoped to a single
client**. A question about client A must never be answered from client B's
dossier — that would be both a confidentiality breach and a correctness
failure. Every query therefore carries a `client_id` filter into Chroma.
"""

from __future__ import annotations

from pathlib import Path

from core import config


SYSTEM_PROMPT = (
    "أنت مساعد قانوني دقيق تعمل داخل مكتب محاماة مصري. "
    "أجب فقط بالاعتماد على السياق المستخرج من ملف القضية. "
    "إذا لم تجد الإجابة في السياق، قل بوضوح إن المعلومة غير متوفرة في المستندات. "
    "لا تخترع وقائع ولا نصوصًا قانونية. أجب باللغة العربية."
)

PROMPT_TEMPLATE = (
    SYSTEM_PROMPT
    + "\n\n=== السياق ===\n{context}\n\n"
    + "=== السؤال ===\n{question}\n\n"
    + "=== الإجابة ===\n"
)


def _embeddings():
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(
        model=config.EMBED_MODEL, base_url=config.OLLAMA_BASE_URL
    )


def get_vectorstore():
    from langchain_chroma import Chroma

    config.VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    return Chroma(
        collection_name=config.COLLECTION_NAME,
        embedding_function=_embeddings(),
        persist_directory=str(config.VECTOR_DIR),
    )


def _chunk(text: str) -> list[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        # Arabic and Latin sentence/paragraph boundaries.
        separators=["\n\n", "\n", "۔", ".", "،", ",", " ", ""],
    )
    return splitter.split_text(text)


def index_text(
    client_id: int,
    text: str,
    source: str,
    category: str = config.DEFAULT_CATEGORY,
) -> int:
    """Embed and store one document's text. Returns the number of chunks."""
    from langchain_core.documents import Document

    text = (text or "").strip()
    if not text:
        return 0

    docs = [
        Document(
            page_content=chunk,
            metadata={
                "client_id": int(client_id),
                "source": source,
                "category": category,
                "chunk": i,
            },
        )
        for i, chunk in enumerate(_chunk(text))
    ]
    get_vectorstore().add_documents(docs)
    return len(docs)


def index_file(client_id: int, path: Path, category: str) -> tuple[int, str]:
    """OCR a file and index it. -> (chunk_count, extracted_text)"""
    from core import ocr

    text = ocr.extract_text(path)
    count = index_text(client_id, text, source=path.name, category=category)
    return count, text


def build_chain(client_id: int, k: int = 4):
    """A retrieval chain hard-scoped to one client's documents."""
    from langchain_ollama import ChatOllama
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.runnables import RunnableParallel, RunnablePassthrough

    retriever = get_vectorstore().as_retriever(
        search_kwargs={"k": k, "filter": {"client_id": int(client_id)}}
    )
    llm = ChatOllama(
        model=config.LLM_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        temperature=0.0,  # legal work: faithfulness over creativity
    )
    prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)

    return RunnableParallel(
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


def _format_docs(docs) -> str:
    return "\n\n---\n\n".join(
        f"[{d.metadata.get('source', '?')}]\n{d.page_content}" for d in docs
    )


def ask(client_id: int, question: str, k: int = 4) -> dict:
    """One-shot query scoped to a single client.

    Returns ``{"answer": str, "sources": [ {...}, ... ]}`` where each source is::

        {"filename": str, "category": str,
         "path": str | None,        # absolute path on disk, if the file exists
         "excerpts": [str, ...]}    # the exact chunks fed to the model

    The source list powers the chat panel's clickable transparency: each entry
    names a document the answer drew on, carries the excerpts used, and resolves
    back to the file on disk so it can be opened for verification.
    """
    result = build_chain(client_id, k=k).invoke(question)
    return {
        "answer": result["answer"].strip(),
        "sources": _collect_sources(client_id, result["sources"]),
    }


def _collect_sources(client_id: int, docs) -> list[dict]:
    """Group retrieved chunks by source document, preserving retrieval order."""
    grouped: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for doc in docs:
        filename = doc.metadata.get("source", "?")
        category = doc.metadata.get("category", config.DEFAULT_CATEGORY)
        key = (filename, category)
        if key not in grouped:
            grouped[key] = {
                "filename": filename,
                "category": category,
                "path": _resolve_source_path(client_id, filename, category),
                "excerpts": [],
            }
            order.append(key)
        excerpt = (doc.page_content or "").strip()
        if excerpt and excerpt not in grouped[key]["excerpts"]:
            grouped[key]["excerpts"].append(excerpt)
    return [grouped[key] for key in order]


def _resolve_source_path(
    client_id: int, filename: str, category: str
) -> str | None:
    """Map a retrieval source back to an absolute path on disk.

    Primary lookup is the documents table (authoritative — it records the exact
    path each file was written to; both imports and mobile uploads store an
    absolute path there). Falls back to reconstructing the path from the dossier
    layout. Returns None if the file cannot be located, in which case the chat
    panel shows the source name without a link.
    """
    from core import database, storage

    row = database.find_document(client_id, filename, category)
    if row is not None:
        candidate = Path(row["rel_path"])
        if candidate.exists():
            return str(candidate.resolve())

    client = database.get_client(client_id)
    if client is not None:
        candidate = (
            storage.client_root(client_id, client["name"]) / category / filename
        )
        if candidate.exists():
            return str(candidate.resolve())
    return None


def delete_client_vectors(client_id: int) -> None:
    """Remove a client's chunks — called when a client record is deleted."""
    try:
        get_vectorstore().delete(where={"client_id": int(client_id)})
    except Exception:
        pass  # store may not exist yet; nothing to clean


def probe() -> tuple[bool, str]:
    """Health check for the Settings screen. -> (healthy, message)"""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"{config.OLLAMA_BASE_URL}/api/tags", timeout=4
        ) as resp:
            names = {m.get("name", "") for m in json.load(resp).get("models", [])}
    except Exception as exc:
        return False, f"Ollama unreachable at {config.OLLAMA_BASE_URL}: {exc}"

    missing = [
        m for m in (config.LLM_MODEL, config.EMBED_MODEL)
        if m not in names and m.split(":")[0] not in {n.split(":")[0] for n in names}
    ]
    if missing:
        return False, "Missing models: " + ", ".join(f"ollama pull {m}" for m in missing)
    return True, f"Ollama ready ({config.LLM_MODEL} + {config.EMBED_MODEL})."
