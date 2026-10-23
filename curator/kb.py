"""Knowledge base: Markdown files with front matter → section chunks with metadata → FAISS index.

Every `##` section of a document becomes one chunk, because a section answers one question and can be
cited as «документ / раздел». Long sections are split further with an overlap.
"""

from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from curator import config

META_KEYS = ("doc_id", "title", "module", "module_title", "topic", "type", "level")
MAX_CHUNK_CHARS = 1500
_splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150)


def parse_doc(text: str) -> tuple[dict, str]:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("документ должен начинаться с front matter (---)")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    meta = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    missing = [k for k in META_KEYS if k not in meta]
    if missing:
        raise ValueError(f"в front matter нет ключей: {', '.join(missing)}")
    meta["module"] = int(meta["module"])
    return meta, "\n".join(lines[end + 1:])


def split_doc(meta: dict, body: str) -> list[Document]:
    sections, heading, buf = [], None, []
    for line in body.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = line[3:].strip(), []
        elif heading is not None:  # text before the first «##» is only the «#» title
            buf.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(buf).strip()))

    chunks = []
    for heading, text in sections:
        if not text:
            continue
        parts = [text] if len(text) <= MAX_CHUNK_CHARS else _splitter.split_text(text)
        for part in parts:
            chunks.append(Document(
                page_content=f"{meta['title']}\n## {heading}\n{part}",
                metadata={**meta, "section": heading, "source": f"{meta['title']} / {heading}"}))
    return chunks


def load_chunks(kb_dir: Path = config.KB_DIR) -> list[Document]:
    chunks = []
    for path in sorted(kb_dir.rglob("*.md")):
        meta, body = parse_doc(path.read_text(encoding="utf-8"))
        chunks.extend(split_doc({**meta, "path": path.relative_to(kb_dir).as_posix()}, body))
    return chunks


def embeddings() -> OpenAIEmbeddings:
    # OpenRouter takes raw text; token arrays (the default) are an OpenAI-only shortcut
    return OpenAIEmbeddings(model=config.EMBED_MODEL, base_url=config.BASE_URL, api_key=config.api_key(),
                            check_embedding_ctx_length=False)


def build_index(chunks: list[Document]) -> FAISS:
    return FAISS.from_documents(chunks, embeddings())


def load_index(index_dir: Path = config.INDEX_DIR) -> FAISS:
    # the index is built by our own ingest.py, so unpickling it is safe
    return FAISS.load_local(str(index_dir), embeddings(), allow_dangerous_deserialization=True)


def make_filter(module: int | None = None, doc_type: str | None = None, level: str | None = None,
                topic: str | None = None):
    """Metadata filter. Organizational docs (module 0) pass the module filter, so a student who narrowed the
    search to one module still gets answers about deadlines and the certificate. Level hides material of the
    other level; «все» is shown to everyone."""
    def keep(meta: dict) -> bool:
        if module is not None and meta["module"] not in (0, module):
            return False
        if doc_type is not None and meta["type"] != doc_type:
            return False
        if level is not None and meta["level"] not in ("все", level):
            return False
        if topic is not None and meta["topic"] != topic:
            return False
        return True
    return keep


def topics(module: int | None = None, kb_dir: Path = config.KB_DIR) -> list[str]:
    """Topics for the search filter, limited to the chosen module (plus organizational docs, as in make_filter)."""
    metas = [parse_doc(p.read_text(encoding="utf-8"))[0] for p in sorted(kb_dir.rglob("*.md"))]
    return sorted({m["topic"] for m in metas if module is None or m["module"] in (0, module)})


def search(index: FAISS, query: str, k: int = config.TOP_K, **filters) -> list[tuple[Document, float]]:
    # the index is small (a few hundred chunks), so filter over all of it instead of a fetch_k sample
    return index.similarity_search_with_score(query, k=k, filter=make_filter(**filters),
                                              fetch_k=index.index.ntotal)
