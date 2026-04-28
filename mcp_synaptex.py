#!/usr/bin/env python3
"""
Serveur MCP Synaptex — expose la recherche sémantique et le contexte projet à Claude Code.

Outils disponibles :
  synaptex_search     : recherche sémantique dans l'index CLAUDE.md
  synaptex_list       : liste tous les projets synced
  synaptex_context    : retourne le contexte d'un ou plusieurs projets
  synaptex_status     : état de l'infrastructure (Ollama, index, projets)
"""
import asyncio
import os
import sys
from pathlib import Path

# Ajouter le répertoire du script au path Python
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

SYNAPTEX_DIR = Path.home() / ".synaptex"
ENV_FILE = SYNAPTEX_DIR / ".env"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def _cfg() -> dict[str, str]:
    env = _load_env()
    for key in ("OLLAMA_BASE_URL", "OLLAMA_EMBED_MODEL", "OLLAMA_FALLBACK_MODEL"):
        if key in os.environ:
            env[key] = os.environ[key]
    if "OLLAMA_HOST" in os.environ:
        env["OLLAMA_BASE_URL"] = os.environ["OLLAMA_HOST"]
    return env


server = Server("synaptex")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="synaptex_search",
            description=(
                "Recherche sémantique dans les CLAUDE.md indexés de tous les projets Forgejo. "
                "Retourne les projets les plus pertinents pour une requête donnée."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Requête de recherche en langage naturel",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Nombre de résultats (défaut: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="synaptex_list",
            description="Liste tous les projets synced depuis Forgejo avec leur stack détectée.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="synaptex_context",
            description=(
                "Retourne le contexte complet d'un ou plusieurs projets "
                "(CLAUDE.md + fiche mémoire). "
                "Sans arguments : retourne l'index global."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "projects": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Noms de projets (correspondance partielle tolérée)",
                    }
                },
            },
        ),
        Tool(
            name="synaptex_status",
            description="Synaptex infrastructure status: Ollama, embed model, index, projects.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    cfg = _cfg()

    if name == "synaptex_search":
        from embed import search as do_search

        query = arguments["query"]
        top_k = arguments.get("top_k", 5)
        host = cfg.get("OLLAMA_BASE_URL", "")
        model = cfg.get("OLLAMA_EMBED_MODEL", "nomic-embed-text-v2-moe")
        fallback = cfg.get("OLLAMA_FALLBACK_MODEL")

        if not host:
            return [TextContent(type="text", text="❌ OLLAMA_BASE_URL non configuré dans ~/.synaptex/.env")]

        try:
            results = do_search(query, host, model, fallback, top_k=top_k)
        except Exception as e:
            return [TextContent(type="text", text=f"❌ Erreur de recherche : {e}")]

        if not results:
            return [TextContent(type="text", text="Aucun résultat trouvé.")]

        lines = [f"## Résultats pour : {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"### [{i}] {r['repo']}/{r['path']} (score={r['score']})")
            lines.append(r["content"][:400])
            lines.append("")

        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "synaptex_list":
        from memory import _detect_stack

        projects_dir = SYNAPTEX_DIR / "projects"
        if not projects_dir.exists():
            return [TextContent(type="text", text="Aucun projet synced. Lancer `synaptex sync` d'abord.")]

        lines = ["## Projets synced\n"]
        for repo_dir in sorted(projects_dir.iterdir()):
            if not repo_dir.is_dir():
                continue
            md = repo_dir / "CLAUDE.md"
            stack = _detect_stack(md.read_text(errors="replace")) if md.exists() else []
            stack_str = f" — {', '.join(stack)}" if stack else ""
            lines.append(f"- **{repo_dir.name}**{stack_str}")

        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "synaptex_context":
        from context import get_context

        projects = tuple(arguments.get("projects", []))
        text = get_context(projects)
        return [TextContent(type="text", text=text)]

    elif name == "synaptex_status":
        import requests

        lines = ["## Synaptex Status\n"]

        host = cfg.get("OLLAMA_BASE_URL", "⚠ non défini")
        lines.append(f"**Ollama host** : {host}")

        if cfg.get("OLLAMA_BASE_URL"):
            try:
                r = requests.get(f"{cfg['OLLAMA_BASE_URL']}/api/tags", timeout=5)
                models = [m["name"] for m in r.json().get("models", [])]
                embed_model = cfg.get("OLLAMA_EMBED_MODEL", "")
                bare_names = {m.split(":")[0] for m in models}
                ok = embed_model in models or embed_model in bare_names
                lines.append(f"**Embed model** : {embed_model} {'✓' if ok else '⚠ absent'}")
                lines.append(f"**Modèles dispo** : {len(models)}")
            except Exception as e:
                lines.append(f"**Ollama** : ✗ {e}")

        from embed import INDEX_DB
        projects_dir = SYNAPTEX_DIR / "projects"
        n_projects = len(list(projects_dir.iterdir())) if projects_dir.exists() else 0
        lines.append(f"**Index DB** : {'✓ existe' if INDEX_DB.exists() else '⚠ vide'}")
        lines.append(f"**Projets synced** : {n_projects}")

        token = cfg.get("FORGEJO_TOKEN", "")
        lines.append(f"**Forgejo token** : {'✓ configuré' if token and token != 'xxx' else '⚠ non configuré'}")

        return [TextContent(type="text", text="\n".join(lines))]

    return [TextContent(type="text", text=f"Outil inconnu : {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
