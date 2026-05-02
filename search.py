"""Pluggable search backend for Synaptex.

Controlled by SYNAPTEX_SEARCH_BACKEND in ~/.synaptex/.env:
  embed (default) — sqlite3 + cosine similarity (zero extra deps, requires Ollama)
  leann           — BM25+vector hybrid via leann-core (pip install leann-core, requires Ollama)
  fts5            — SQLite FTS5 keyword search (zero deps, works fully offline)

All backends expose the same two functions:
  rebuild_index(projects_dir, cfg) -> int  (returns doc/chunk count)
  search(query, cfg, top_k)        -> list[dict]  ({repo, path, content, score})
"""
import sqlite3
from pathlib import Path
from typing import Optional

SYNAPTEX_DIR = Path.home() / ".synaptex"
FTS5_DB = SYNAPTEX_DIR / "leann_index" / "index.db"   # same DB as embed.py
LEANN_INDEX_DIR = SYNAPTEX_DIR / "leann_index" / "leann"


# ---------------------------------------------------------------------------
# FTS5 backend
# ---------------------------------------------------------------------------

def _fts5_db() -> sqlite3.Connection:
    FTS5_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(FTS5_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS docs "
        "(id INTEGER PRIMARY KEY, repo TEXT, path TEXT, chunk INTEGER DEFAULT 0, "
        "content TEXT, embedding BLOB, UNIQUE(repo, path, chunk))"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts "
        "USING fts5(repo UNINDEXED, path UNINDEXED, content, tokenize='unicode61')"
    )
    conn.commit()
    return conn


def fts5_rebuild(projects_dir: Path, cfg: dict) -> int:
    """Re-index all CLAUDE.md into FTS5 table. Returns document count."""
    conn = _fts5_db()
    conn.execute("DELETE FROM docs_fts")
    conn.commit()

    count = 0
    for md_file in projects_dir.rglob("CLAUDE.md"):
        repo = md_file.parts[len(projects_dir.parts)]
        path = str(md_file.relative_to(projects_dir / repo))
        content = md_file.read_text(errors="replace")
        conn.execute(
            "INSERT INTO docs_fts (repo, path, content) VALUES (?,?,?)",
            (repo, path, content),
        )
        count += 1

    conn.commit()
    conn.close()
    return count


def fts5_search(query: str, cfg: dict, top_k: int = 5) -> list[dict]:
    """Keyword search via SQLite FTS5. No Ollama required."""
    conn = _fts5_db()
    try:
        rows = conn.execute(
            "SELECT repo, path, content, rank "
            "FROM docs_fts WHERE docs_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query, top_k),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 MATCH syntax error — fall back to LIKE
        rows = conn.execute(
            "SELECT repo, path, content, 0.0 "
            "FROM docs_fts WHERE content LIKE ? LIMIT ?",
            (f"%{query}%", top_k),
        ).fetchall()
    conn.close()

    results = []
    for repo, path, content, rank in rows:
        # FTS5 rank is negative (lower = better); normalize to 0-1
        score = round(1.0 / (1.0 + abs(rank)), 4) if rank else 0.5
        results.append({"repo": repo, "path": path, "content": content[:500], "score": score})
    return results


# ---------------------------------------------------------------------------
# LEANN backend (BM25+vector hybrid)
# ---------------------------------------------------------------------------

def _leann_check_import():
    """Raise ImportError with instructions if leann-core is not installed."""
    try:
        import leann  # noqa: PLC0415, F401
    except ImportError:
        raise ImportError(
            "leann-core is not installed.\n"
            "Install it with:  pip install leann-core leann-backend-hnsw\n"
            "Or switch backend: SYNAPTEX_SEARCH_BACKEND=embed in ~/.synaptex/.env"
        )


def leann_rebuild(projects_dir: Path, cfg: dict) -> int:
    """Build LEANN index (BM25+vector). Returns document count."""
    _leann_check_import()
    from leann import LeannBuilder  # noqa: PLC0415

    ollama_url = cfg.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = cfg.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    api_type = cfg.get("OLLAMA_API_TYPE", "ollama")
    LEANN_INDEX_DIR.mkdir(parents=True, exist_ok=True)

    if api_type == "openai":
        embedding_mode = "openai"
        embedding_options = {"base_url": ollama_url, "api_key": cfg.get("OLLAMA_API_KEY", "")}
    else:
        embedding_mode = "ollama"
        embedding_options = {"host": ollama_url}

    builder = LeannBuilder(
        backend_name="hnsw",
        embedding_model=model,
        embedding_mode=embedding_mode,
        embedding_options=embedding_options,
    )
    count = 0
    for md_file in projects_dir.rglob("CLAUDE.md"):
        repo = md_file.parts[len(projects_dir.parts)]
        path = str(md_file.relative_to(projects_dir / repo))
        content = md_file.read_text(errors="replace")
        builder.add_text(content, metadata={"repo": repo, "path": path})
        count += 1
    if count:
        builder.build_index(str(LEANN_INDEX_DIR))
    return count


def leann_search(query: str, cfg: dict, top_k: int = 5) -> list[dict]:
    """BM25+vector hybrid search via leann-core."""
    _leann_check_import()
    from leann import LeannSearcher  # noqa: PLC0415

    meta_file = LEANN_INDEX_DIR / "leann.meta.json"
    if not meta_file.exists():
        return []

    searcher = LeannSearcher(str(LEANN_INDEX_DIR))
    raw = searcher.search(query, top_k=top_k)
    results = []
    for item in raw:
        meta = item.metadata or {}
        results.append({
            "repo": meta.get("repo", ""),
            "path": meta.get("path", ""),
            "content": item.text[:500],
            "score": round(item.score, 4),
        })
    return results


# ---------------------------------------------------------------------------
# Router — public API
# ---------------------------------------------------------------------------

def rebuild_index(projects_dir: Path, cfg: dict) -> tuple[int, str]:
    """Route to the configured search backend. Returns (count, backend_used).

    Falls back to embed if leann is configured but not installed.
    """
    backend = cfg.get("SYNAPTEX_SEARCH_BACKEND", "embed")
    if backend == "leann":
        try:
            _leann_check_import()
            return leann_rebuild(projects_dir, cfg), "leann"
        except ImportError as exc:
            import warnings
            warnings.warn(
                f"leann non disponible — fallback sur embed. Cause : {exc}",
                stacklevel=2,
            )
            backend = "embed"
    if backend == "fts5":
        return fts5_rebuild(projects_dir, cfg), "fts5"
    # embed (default ou fallback)
    from embed import rebuild_index as _embed_rebuild  # noqa: PLC0415
    count = _embed_rebuild(
        projects_dir,
        ollama_host=cfg.get("OLLAMA_BASE_URL", ""),
        model=cfg.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        fallback_model=cfg.get("OLLAMA_FALLBACK_MODEL") or None,
        api_type=cfg.get("OLLAMA_API_TYPE", "ollama"),
        api_key=cfg.get("OLLAMA_API_KEY") or None,
    )
    return count, "embed"


def search(query: str, cfg: dict, top_k: int = 5) -> list[dict]:
    """Route to the configured search backend. Falls back to embed if leann missing."""
    backend = cfg.get("SYNAPTEX_SEARCH_BACKEND", "embed")
    if backend == "leann":
        try:
            _leann_check_import()
            return leann_search(query, cfg, top_k)
        except ImportError as exc:
            import warnings
            warnings.warn(
                f"leann non disponible — fallback sur embed. Cause : {exc}",
                stacklevel=2,
            )
            backend = "embed"
    if backend == "fts5":
        return fts5_search(query, cfg, top_k)
    from embed import search as _embed_search  # noqa: PLC0415
    return _embed_search(
        query,
        ollama_host=cfg.get("OLLAMA_BASE_URL", ""),
        model=cfg.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
        fallback_model=cfg.get("OLLAMA_FALLBACK_MODEL") or None,
        top_k=top_k,
        api_type=cfg.get("OLLAMA_API_TYPE", "ollama"),
        api_key=cfg.get("OLLAMA_API_KEY") or None,
    )
