"""Lightweight vector index — remote Ollama embeddings + sqlite3 + cosine similarity.

Chunking: each document is split into ~400-word overlapping chunks so long
CLAUDE.md files are fully represented in the index.

Embedding providers:
  ollama (default) — POST {host}/api/embed
  openai           — POST {host}/v1/embeddings  (OpenAI-compatible: LM Studio, vLLM, LocalAI…)

Set OLLAMA_API_TYPE=openai in ~/.synaptex/.env to use the OpenAI-compatible endpoint.
"""
import sqlite3
import struct
from pathlib import Path
from typing import Optional

import requests

SYNAPTEX_DIR = Path.home() / ".synaptex"
INDEX_DB = SYNAPTEX_DIR / "leann_index" / "index.db"

CHUNK_WORDS = 400
OVERLAP_WORDS = 50


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(data: bytes) -> list[float]:
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _chunks(text: str, size: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    """Découpe le texte en morceaux de `size` mots avec chevauchement."""
    words = text.split()
    if len(words) <= size:
        return [text]
    step = size - overlap
    return [
        " ".join(words[i : i + size])
        for i in range(0, len(words) - overlap, step)
        if words[i : i + size]
    ]


def _db() -> sqlite3.Connection:
    INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(INDEX_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS docs "
        "(id INTEGER PRIMARY KEY, repo TEXT, path TEXT, chunk INTEGER DEFAULT 0, "
        "content TEXT, embedding BLOB, "
        "UNIQUE(repo, path, chunk))"
    )
    conn.commit()
    return conn


def embed_text(
    text: str,
    ollama_host: str,
    model: str,
    fallback_model: Optional[str] = None,
    api_type: str = "ollama",
    api_key: Optional[str] = None,
) -> list[float]:
    """Call the configured embedding API and return a float vector.

    api_type='ollama'  → POST {host}/api/embed   (Ollama native)
    api_type='openai'  → POST {host}/v1/embeddings  (OpenAI-compatible)
    """
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for m in ([model] + ([fallback_model] if fallback_model else [])):
        try:
            if api_type == "openai":
                r = requests.post(
                    f"{ollama_host}/v1/embeddings",
                    json={"model": m, "input": text},
                    headers=headers,
                    timeout=30,
                )
                r.raise_for_status()
                data = r.json()
                items = data.get("data", [])
                if items and items[0].get("embedding"):
                    return items[0]["embedding"]
            else:  # ollama native
                r = requests.post(
                    f"{ollama_host}/api/embed",
                    json={"model": m, "input": text},
                    headers=headers,
                    timeout=30,
                )
                r.raise_for_status()
                data = r.json()
                embeddings = data.get("embeddings", [])
                if embeddings and embeddings[0]:
                    return embeddings[0]
        except Exception:
            continue
    raise RuntimeError(f"No embedding model available at {ollama_host}")


def index_document(
    repo: str,
    path: str,
    content: str,
    ollama_host: str,
    model: str,
    fallback_model: Optional[str] = None,
    api_type: str = "ollama",
    api_key: Optional[str] = None,
) -> int:
    """Index a document by chunks. Returns the number of chunks created."""
    conn = _db()
    conn.execute("DELETE FROM docs WHERE repo=? AND path=?", (repo, path))
    conn.commit()

    parts = _chunks(content)
    for i, chunk in enumerate(parts):
        vec = embed_text(chunk, ollama_host, model, fallback_model, api_type, api_key)
        conn.execute(
            "INSERT INTO docs (repo, path, chunk, content, embedding) VALUES (?,?,?,?,?)",
            (repo, path, i, chunk, _pack(vec)),
        )
    conn.commit()
    conn.close()
    return len(parts)


def search(
    query: str,
    ollama_host: str,
    model: str,
    fallback_model: Optional[str] = None,
    top_k: int = 5,
    api_type: str = "ollama",
    api_key: Optional[str] = None,
) -> list[dict]:
    """Semantic search — returns top_k chunks with cosine score.

    Chunks from the same document are deduplicated: only the best score
    per (repo, path) is kept in the results.
    """
    query_vec = embed_text(query, ollama_host, model, fallback_model, api_type, api_key)
    conn = _db()
    rows = conn.execute("SELECT repo, path, chunk, content, embedding FROM docs").fetchall()
    conn.close()

    best: dict[tuple, dict] = {}
    for repo, path, chunk, content, emb_bytes in rows:
        score = _cosine(query_vec, _unpack(emb_bytes))
        key = (repo, path)
        if key not in best or score > best[key]["score"]:
            best[key] = {
                "repo": repo,
                "path": path,
                "chunk": chunk,
                "content": content[:500],
                "score": round(score, 4),
            }

    results = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def rebuild_index(
    projects_dir: Path,
    ollama_host: str,
    model: str,
    fallback_model: Optional[str] = None,
    api_type: str = "ollama",
    api_key: Optional[str] = None,
) -> int:
    """Re-index all CLAUDE.md files in projects_dir. Returns total chunk count."""
    total_chunks = 0
    for md_file in projects_dir.rglob("CLAUDE.md"):
        repo = md_file.parts[len(projects_dir.parts)]
        path = str(md_file.relative_to(projects_dir / repo))
        content = md_file.read_text(errors="replace")
        total_chunks += index_document(repo, path, content, ollama_host, model, fallback_model, api_type, api_key)
    return total_chunks
