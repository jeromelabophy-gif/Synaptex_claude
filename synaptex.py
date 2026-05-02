#!/usr/bin/env python3
"""Synaptex CLI — global brain for all your Claude Code projects."""
import os
import sys
from pathlib import Path

import click

SYNAPTEX_DIR = Path.home() / ".synaptex"
ENV_FILE = SYNAPTEX_DIR / ".env"


def _load_env() -> dict[str, str]:
    """Parse ~/.synaptex/.env without external dependencies."""
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip()
    return env


def _cfg() -> dict[str, str]:
    """Merge shell environment + .env (shell takes priority)."""
    env = _load_env()
    for key in (
        "FORGE_URL", "FORGE_TOKEN", "FORGE_USER", "FORGE_TYPE",
        "FORGEJO_URL", "FORGEJO_TOKEN", "FORGEJO_USER",  # backwards compat
        "OLLAMA_BASE_URL", "OLLAMA_EMBED_MODEL", "OLLAMA_FALLBACK_MODEL",
        "OLLAMA_API_TYPE", "OLLAMA_API_KEY",
        "SYNAPTEX_INCLUDE_PATTERNS", "LOCAL_REPOS_PATH", "SYNAPTEX_SEARCH_BACKEND",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    # ollama_select exports OLLAMA_HOST → overrides OLLAMA_BASE_URL
    if "OLLAMA_HOST" in os.environ:
        env["OLLAMA_BASE_URL"] = os.environ["OLLAMA_HOST"]
    # FORGEJO_* → FORGE_* backwards compat
    for old, new in [("FORGEJO_URL", "FORGE_URL"), ("FORGEJO_TOKEN", "FORGE_TOKEN"), ("FORGEJO_USER", "FORGE_USER")]:
        if old in env and new not in env:
            env[new] = env[old]
    return env


@click.group()
def cli():
    """Synaptex — global hypercontext for your Claude Code projects."""


_TOKEN_HELP = {
    "forgejo": "Ton instance → Paramètres → Applications → Générer un token  (scope: read:repository)",
    "gitea":   "Ton instance → Paramètres → Applications → Générer un token  (scope: read:repository)",
    "github":  "github.com → Settings → Developer settings → Personal access tokens → scope: repo (read)",
    "gitlab":  "gitlab.com → Préférences → Access Tokens → scope: read_api",
}

_PATTERN_CHOICES = {
    "1": "CLAUDE.md",
    "2": "CLAUDE.md,README.md",
    "3": "CLAUDE.md,README.md,MEMORY.md",
    "4": "CLAUDE.md,README.md,MEMORY.md,GRAPH_REPORT.md",
    "5": "CLAUDE.md,project.md",
    "6": "*.md",
}
_PATTERN_LABELS = {
    "1": "CLAUDE.md uniquement (défaut)",
    "2": "CLAUDE.md + README.md",
    "3": "CLAUDE.md + README.md + MEMORY.md",
    "4": "CLAUDE.md + README.md + MEMORY.md + GRAPH_REPORT.md",
    "5": "CLAUDE.md + project.md (Obsidian vault)",
    "6": "Tous les fichiers .md",
    "7": "Personnalisé",
}


@cli.command()
def init():
    """Interactive wizard to create ~/.synaptex/.env."""
    click.echo("=== Synaptex Init ===\n")
    click.echo("This wizard creates ~/.synaptex/.env with your configuration.\n")

    if ENV_FILE.exists():
        existing = _load_env()
        if existing.get("FORGE_TOKEN") or existing.get("FORGEJO_TOKEN") or existing.get("LOCAL_REPOS_PATH"):
            if not click.confirm(".env already configured. Overwrite?", default=False):
                click.echo("Aborted.")
                return

    # Git provider
    forge_type = click.prompt(
        "Git provider",
        type=click.Choice(["forgejo", "gitea", "github", "gitlab", "local"]),
        default="forgejo",
    )

    forge_url = ""
    forge_token = ""
    forge_user = ""
    local_repos_path = ""

    if forge_type == "local":
        local_repos_path = click.prompt(
            "Chemin vers ton dossier de repos locaux",
            default=str(Path.home() / "projects"),
        )
    elif forge_type == "github":
        forge_url = "https://api.github.com"
        click.echo("(URL GitHub définie automatiquement)")
        if help_text := _TOKEN_HELP.get(forge_type):
            click.echo(f"  ℹ  {help_text}")
        forge_token = click.prompt("API token (read access to repos)", hide_input=True)
        forge_user = click.prompt("Username / login")
    else:
        forge_url = click.prompt(
            "Forge URL",
            default="http://localhost:3000" if forge_type in ("forgejo", "gitea") else "https://gitlab.com",
        )
        if help_text := _TOKEN_HELP.get(forge_type):
            click.echo(f"  ℹ  {help_text}")
        forge_token = click.prompt("API token (read access to repos)", hide_input=True)
        forge_user = click.prompt("Username / login")

    # Ollama
    click.echo("\n--- Embedding provider ---")
    click.echo("Supports: Ollama (native), or any OpenAI-compatible API (LM Studio, vLLM, LocalAI…)")

    ollama_url = click.prompt("Embedding API base URL", default="http://localhost:11434")
    api_type = click.prompt(
        "API type",
        type=click.Choice(["ollama", "openai"]),
        default="ollama",
    )

    # Try to list available models
    suggested_model = "nomic-embed-text"
    try:
        import requests as _req
        if api_type == "ollama":
            r = _req.get(f"{ollama_url}/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
        else:
            r = _req.get(f"{ollama_url}/v1/models", timeout=5)
            models = [m["id"] for m in r.json().get("data", [])]
        if models:
            click.echo(f"\nAvailable models: {', '.join(models[:8])}")
            embed_candidates = [m for m in models if "embed" in m.lower() or "nomic" in m.lower()]
            if embed_candidates:
                suggested_model = embed_candidates[0]
    except Exception:
        pass

    embed_model = click.prompt("Embedding model", default=suggested_model)
    fallback_model = click.prompt("Fallback model (optional, Enter to skip)", default="")

    api_key = ""
    if api_type == "openai":
        api_key = click.prompt("API key (optional, Enter to skip)", default="", hide_input=True)

    # Search backend
    click.echo("\n--- Search backend ---")
    leann_available = False
    try:
        import leann  # noqa: F401
        leann_available = True
    except ImportError:
        pass
    backend_choices = ["embed", "leann", "fts5"] if leann_available else ["embed", "fts5"]
    if not leann_available:
        click.echo("(leann not available — install with: pip install leann-core)")
    search_backend = click.prompt(
        "Search backend (embed=semantic, fts5=offline keyword, leann=BM25+vector)",
        type=click.Choice(backend_choices),
        default="embed",
    )

    # Include patterns
    click.echo("\n--- Fichiers à indexer ---")
    for k, label in _PATTERN_LABELS.items():
        click.echo(f"  {k}) {label}")
    pattern_choice = click.prompt(
        "Include patterns",
        default="1",
        type=click.Choice(["1", "2", "3", "4", "5", "6"]),
    )
    if pattern_choice == "6":
        include_patterns = click.prompt("Patterns (séparés par virgule)", default="CLAUDE.md")
    else:
        include_patterns = _PATTERN_CHOICES[pattern_choice]

    # Write .env
    SYNAPTEX_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"FORGE_TYPE={forge_type}",
    ]
    if forge_url:
        lines.append(f"FORGE_URL={forge_url}")
    if forge_token:
        lines.append(f"FORGE_TOKEN={forge_token}")
    if forge_user:
        lines.append(f"FORGE_USER={forge_user}")
    lines += [
        f"OLLAMA_BASE_URL={ollama_url}",
        f"OLLAMA_API_TYPE={api_type}",
        f"OLLAMA_EMBED_MODEL={embed_model}",
        f"SYNAPTEX_SEARCH_BACKEND={search_backend}",
        f"SYNAPTEX_INCLUDE_PATTERNS={include_patterns}",
    ]
    if local_repos_path:
        lines.append(f"LOCAL_REPOS_PATH={local_repos_path}")
    if fallback_model:
        lines.append(f"OLLAMA_FALLBACK_MODEL={fallback_model}")
    if api_key:
        lines.append(f"OLLAMA_API_KEY={api_key}")

    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)

    click.echo(f"\n✓ {ENV_FILE} écrit (chmod 600)\n")
    click.echo("=== Configuration ===")
    click.echo(f"  Git provider   : {forge_type}")
    if forge_type == "local":
        if local_repos_path:
            click.echo(f"  Repos path     : {local_repos_path}")
    else:
        click.echo(f"  Forge URL      : {forge_url}")
        click.echo(f"  Forge token    : ✓ configuré")
        click.echo(f"  Forge user     : {forge_user}")
    click.echo(f"  Include files  : {include_patterns}")
    click.echo(f"  Embedding URL  : {ollama_url}")
    click.echo(f"  Embed model    : {embed_model}")
    click.echo(f"  Search backend : {search_backend}")
    click.echo("")
    click.echo("Prochaines étapes :")
    click.echo("  synaptex status          — vérifier la connectivité")
    click.echo("  synaptex sync --dry-run  — prévisualiser la synchronisation")
    click.echo("  synaptex sync            — synchronisation complète + index")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Lister sans écrire")
@click.option("--no-index", is_flag=True, help="Ne pas relancer l'indexation")
@click.option("--exclude", multiple=True, metavar="REPO",
              help="Exclure des repos par nom (répétable). Ex: --exclude tests --exclude sandbox")
@click.option("--only", default=None, metavar="REPO",
              help="Ne syncer qu'un seul repo. Ex: --only mon-projet")
def sync(dry_run: bool, no_index: bool, exclude: tuple, only: str | None):
    """Sync files from the git provider and re-index."""
    from forge import sync_all
    from search import rebuild_index
    from memory import generate_memory_sheet

    cfg = _cfg()
    forge_type = cfg.get("FORGE_TYPE", "forgejo")
    forge_url = cfg.get("FORGE_URL", "")
    forge_token = cfg.get("FORGE_TOKEN", "")
    forge_user = cfg.get("FORGE_USER", "")
    local_path = cfg.get("LOCAL_REPOS_PATH", "")
    raw_patterns = cfg.get("SYNAPTEX_INCLUDE_PATTERNS", "CLAUDE.md")
    include_patterns = [p.strip() for p in raw_patterns.split(",") if p.strip()]
    raw_exclude_dirs = cfg.get("SYNAPTEX_EXCLUDE_DIRS", "")
    exclude_dirs = [d.strip() for d in raw_exclude_dirs.split(",") if d.strip()]

    if forge_type != "local":
        for label, val in [("FORGE_URL / FORGEJO_URL", forge_url), ("FORGE_TOKEN / FORGEJO_TOKEN", forge_token)]:
            if not val or val in ("xxx", "your-token-here"):
                click.echo(
                    f"❌ {label} not configured in {ENV_FILE}\n"
                    f"   Run `synaptex init` or edit the file.",
                    err=True,
                )
                sys.exit(1)

    if only and only in exclude:
        click.echo(f"⚠ --only '{only}' est aussi dans --exclude : aucun repo ne sera synchronisé.", err=True)
        sys.exit(1)

    src = local_path if forge_type == "local" else forge_url
    click.echo(f"{'[DRY-RUN] ' if dry_run else ''}Syncing from {forge_type}: {src}…")

    result = sync_all(
        forge_url=forge_url,
        token=forge_token,
        user=forge_user,
        dry_run=dry_run,
        forge_type=forge_type,
        local_repos_path=local_path,
        include_patterns=include_patterns,
        exclude=list(exclude),
        exclude_dirs=exclude_dirs,
        only=only,
    )

    click.echo(f"  ✓ {len(result['synced'])} files synced")
    click.echo(f"  ↷ {len(result['skipped'])} repos sans fichiers correspondants")
    if result["warnings"]:
        click.echo(f"  ⚠ {len(result['warnings'])} suspect patterns:", err=True)
        for w in result["warnings"][:5]:
            click.echo(f"    {w}", err=True)

    if dry_run or no_index:
        return

    # Generate memory sheets for each synced file
    projects_dir = SYNAPTEX_DIR / "projects"
    for item in result["synced"]:
        md = projects_dir / item["repo"] / item["path"]
        if md.exists():
            generate_memory_sheet(item["repo"], md.read_text(errors="replace"))

    # Re-index via configured search backend
    backend = cfg.get("SYNAPTEX_SEARCH_BACKEND", "embed")
    ollama_host = cfg.get("OLLAMA_BASE_URL", "")

    if backend == "fts5":
        click.echo("  Indexing (FTS5 keyword mode — no Ollama required)…")
    elif not ollama_host:
        click.echo("  ⚠ OLLAMA_BASE_URL not set — skipping index (set SYNAPTEX_SEARCH_BACKEND=fts5 for offline)")
        return
    else:
        model = cfg.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        click.echo(f"  Indexing via {ollama_host} ({model}, backend={backend})…")

    import warnings as _warnings
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        try:
            count, backend_used = rebuild_index(projects_dir, cfg)
            if caught:
                for w in caught:
                    click.echo(f"  ⚠ {w.message}", err=True)
                click.echo(f"  ✓ {count} documents indexés (fallback: {backend_used})")
            else:
                click.echo(f"  ✓ {count} documents indexés ({backend_used})")
        except Exception as exc:
            click.echo(f"  ✗ Indexation échouée : {exc}", err=True)
            sys.exit(1)


@cli.command(name="map")
def map_cmd():
    """Generate ~/.synaptex/index.md with a Mermaid dependency graph."""
    from memory import generate_index
    dest = generate_index()
    click.echo(f"✓ Map generated → {dest}")


@cli.command()
@click.argument("projects", nargs=-1)
def context(projects: tuple[str, ...]):
    """Generate injectable context block. Ex: $(synaptex context myproject)"""
    from context import get_context
    click.echo(get_context(projects))


@cli.command()
@click.argument("query")
@click.option("-k", "--top", default=5, show_default=True, help="Number of results")
def search(query: str, top: int):
    """Semantic or keyword search across indexed CLAUDE.md files."""
    from search import search as do_search
    cfg = _cfg()
    backend = cfg.get("SYNAPTEX_SEARCH_BACKEND", "embed")

    if backend not in ("fts5",) and not cfg.get("OLLAMA_BASE_URL"):
        click.echo("❌ OLLAMA_BASE_URL not configured (or use SYNAPTEX_SEARCH_BACKEND=fts5 for offline)", err=True)
        sys.exit(1)

    results = do_search(query, cfg, top_k=top)
    if not results:
        click.echo(f"No results for '{query}'")
        return
    for i, r in enumerate(results, 1):
        click.echo(f"\n[{i}] {r['repo']}/{r['path']} (score={r['score']})")
        click.echo(f"    {r['content'][:200]}")


@cli.command()
@click.option("--all", "all_caches", is_flag=True, help="Purge projects/ ET memory/")
@click.option("--memory", "only_memory", is_flag=True, help="Purge uniquement memory/")
@click.option("--projects", "only_projects", is_flag=True, help="Purge uniquement projects/")
def clean(all_caches: bool, only_memory: bool, only_projects: bool):
    """Purge les caches locaux de Synaptex."""
    import shutil

    if not any([all_caches, only_memory, only_projects]):
        click.echo("Précise ce que tu veux purger :")
        click.echo("  synaptex clean --all       — projects/ + memory/")
        click.echo("  synaptex clean --projects  — projets synced uniquement")
        click.echo("  synaptex clean --memory    — fiches mémoire uniquement")
        return

    targets = []
    if all_caches or only_projects:
        targets.append(SYNAPTEX_DIR / "projects")
    if all_caches or only_memory:
        targets.append(SYNAPTEX_DIR / "memory")

    for target in targets:
        if target.exists():
            count = sum(1 for _ in target.rglob("*") if _.is_file())
            shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
            click.echo(f"  ✓ {target.name}/ purgé ({count} fichiers supprimés)")
        else:
            click.echo(f"  = {target.name}/ déjà vide")


@cli.command()
def status():
    """Show Synaptex infrastructure status."""
    import requests
    cfg = _cfg()

    click.echo("=== Synaptex Status ===\n")

    # --- Git provider ---
    forge_type = cfg.get("FORGE_TYPE", "forgejo")
    token = cfg.get("FORGE_TOKEN", "")
    click.echo(f"Forge type    : {forge_type}")
    if forge_type == "local":
        click.echo(f"Local repos   : {cfg.get('LOCAL_REPOS_PATH', '⚠ not set')}")
    else:
        click.echo(f"Forge URL     : {cfg.get('FORGE_URL', '⚠ not set')}")
        click.echo(f"Forge token   : {'✓ set' if token and token not in ('xxx', 'your-token-here') else '⚠ not configured'}")

    # --- Ollama ---
    host = cfg.get("OLLAMA_BASE_URL", "")
    api_type = cfg.get("OLLAMA_API_TYPE", "ollama")
    embed_model = cfg.get("OLLAMA_EMBED_MODEL", "")
    click.echo(f"\nOllama host   : {host or '⚠ not set'}")
    if host:
        try:
            if api_type == "openai":
                r = requests.get(f"{host}/v1/models", timeout=5)
                models = [m["id"] for m in r.json().get("data", [])]
            else:
                r = requests.get(f"{host}/api/tags", timeout=5)
                models = [m["name"] for m in r.json().get("models", [])]
            model_names_bare = {m.split(":")[0] for m in models}
            ok = "✓" if (embed_model in models or embed_model in model_names_bare) else "⚠ not found"
            click.echo(f"Ollama        : ✓ {len(models)} models")
            click.echo(f"Embed model   : {embed_model} [{ok}]")
        except Exception as e:
            click.echo(f"Ollama        : ✗ {e}")

    # --- Search backend ---
    backend = cfg.get("SYNAPTEX_SEARCH_BACKEND", "embed")
    if backend == "leann":
        try:
            from search import _leann_check_import
            _leann_check_import()
            backend_status = "✓ disponible"
        except ImportError:
            backend_status = "⚠ non installé (pip install leann-core leann-backend-hnsw) — fallback: embed"
    else:
        backend_status = "✓"
    click.echo(f"\nSearch backend: {backend} [{backend_status}]")

    # --- Index ---
    from embed import INDEX_DB
    if INDEX_DB.exists():
        import sqlite3 as _sqlite3
        try:
            conn = _sqlite3.connect(INDEX_DB)
            doc_count = conn.execute(
                "SELECT COUNT(DISTINCT repo || '/' || path) FROM docs"
            ).fetchone()[0]
            conn.close()
            size_kb = INDEX_DB.stat().st_size // 1024
            click.echo(f"Index DB      : ✓ {doc_count} documents | {size_kb} KB")
        except Exception:
            click.echo(f"Index DB      : {INDEX_DB} (illisible)")
    else:
        click.echo(f"Index DB      : ⚠ vide (lancer synaptex sync)")

    # --- Memory sheets ---
    memory_dir = SYNAPTEX_DIR / "memory"
    memory_count = len(list(memory_dir.glob("*.md"))) if memory_dir.exists() else 0
    click.echo(f"Memory sheets : {memory_count} fiches")

    # --- Projets synced ---
    projects_dir = SYNAPTEX_DIR / "projects"
    projects = list(projects_dir.iterdir()) if projects_dir.exists() else []
    click.echo(f"Projects sync : {len(projects)} repos")

    # --- Dernier sync ---
    from forge import SYNC_LOG
    if SYNC_LOG.exists():
        lines = SYNC_LOG.read_text().splitlines()
        last_lines = [l for l in reversed(lines) if "sync_all done" in l]
        if last_lines:
            ts = last_lines[0].split("  ")[0]
            click.echo(f"Dernier sync  : {ts}")
        else:
            click.echo(f"Dernier sync  : (log présent, aucun sync terminé)")
    else:
        click.echo(f"Dernier sync  : jamais")


def _split_paths(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(":") if p.strip()]


def _normalize_path(p: str) -> str:
    return str(Path(p).expanduser().resolve())


def _write_local_repos_path(paths: list[str]) -> None:
    """Rewrite LOCAL_REPOS_PATH in ~/.synaptex/.env, preserving other lines."""
    SYNAPTEX_DIR.mkdir(parents=True, exist_ok=True)
    new_value = ":".join(paths)
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"LOCAL_REPOS_PATH={new_value}\n")
        return
    lines = ENV_FILE.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("LOCAL_REPOS_PATH="):
            lines[i] = f"LOCAL_REPOS_PATH={new_value}"
            found = True
            break
    if not found:
        lines.append(f"LOCAL_REPOS_PATH={new_value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


@cli.command()
def paths():
    """List configured local repo paths."""
    cfg = _cfg()
    raw = cfg.get("LOCAL_REPOS_PATH", "")
    items = _split_paths(raw)
    if not items:
        click.echo("(no LOCAL_REPOS_PATH configured)")
        return
    for p in items:
        resolved = _normalize_path(p)
        mark = "✓" if Path(resolved).exists() else "✗"
        click.echo(f"  {mark} {resolved}")


@cli.command()
@click.argument("path")
def add(path: str):
    """Add a local repo path to LOCAL_REPOS_PATH (dedup, persisted in .env)."""
    new = _normalize_path(path)
    if not Path(new).exists():
        click.echo(f"⚠ Path does not exist: {new}", err=True)
        sys.exit(1)
    cfg = _cfg()
    current = [_normalize_path(p) for p in _split_paths(cfg.get("LOCAL_REPOS_PATH", ""))]
    if new in current:
        click.echo(f"= already present: {new}")
        return
    current.append(new)
    _write_local_repos_path(current)
    click.echo(f"✓ added: {new}")
    click.echo(f"  LOCAL_REPOS_PATH now has {len(current)} path(s)")


@cli.command()
@click.argument("path")
def remove(path: str):
    """Remove a local repo path from LOCAL_REPOS_PATH."""
    target = _normalize_path(path)
    cfg = _cfg()
    current = [_normalize_path(p) for p in _split_paths(cfg.get("LOCAL_REPOS_PATH", ""))]
    if target not in current:
        click.echo(f"✗ not present: {target}", err=True)
        sys.exit(1)
    current.remove(target)
    _write_local_repos_path(current)
    click.echo(f"✓ removed: {target}")
    click.echo(f"  LOCAL_REPOS_PATH now has {len(current)} path(s)")


if __name__ == "__main__":
    cli()
