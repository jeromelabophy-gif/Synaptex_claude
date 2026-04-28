# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Cortex is a Python CLI (Click) that centralizes `CLAUDE.md` files from all your projects, builds a semantic index via a remote or local embedding provider, and exposes everything as an MCP server to Claude Code.

Runtime state lives in `~/.synaptex/` (outside the repo). The repo contains only code.

## Development Commands

```bash
# Run the CLI from the project directory
python3 cortex.py <command>

# Or via the installed wrapper
~/.local/bin/cortex <command>

# Available commands
python3 cortex.py init             # interactive setup wizard
python3 cortex.py status           # connectivity: forge + Ollama + index
python3 cortex.py sync --dry-run   # validate without writing
python3 cortex.py sync             # sync forge → memory sheets + index
python3 cortex.py map              # generate ~/.synaptex/index.md (Mermaid)
python3 cortex.py context [repos]  # injectable context block → stdout
python3 cortex.py search "query"   # search via configured backend
```

No automated tests — validate with `sync --dry-run` then `status`.

## Architecture

### Data Flow

```
Forge API / local disk → forge.py  → ~/.synaptex/projects/<repo>/CLAUDE.md
                                   → memory.py → ~/.synaptex/memory/<repo>.md
                                   → search.py → index (embed / leann / fts5)
                                                  (embeddings via Ollama or OpenAI-compatible)
```

### Modules

| File | Role |
|---|---|
| `cortex.py` | Click CLI, `.env` loading, orchestration |
| `forge.py` | Multi-forge bridge: Forgejo/Gitea (API v1), GitHub (API v3), GitLab (API v4), Local (disk scan) |
| `embed.py` | sqlite3 vector index: 400-word chunks / 50-word overlap, `<Nf` float32, cosine similarity, OpenAI-compatible API support |
| `search.py` | Search backend router: `embed` (default) / `leann` (BM25+vector) / `fts5` (offline keyword) |
| `memory.py` | Stack detection by regex, `.md` memory sheets, Mermaid wikilinks graph |
| `context.py` | Aggregates index.md + memory sheets → injectable stdout block |
| `mcp_cortex.py` | MCP stdio server (`mcp` package): 4 tools exposed to Claude Code |
| `install.sh` | Full setup: Bun + qmd, `claude mcp add`, `~/.local/bin/cortex` wrapper |

### Key Design Decisions

- **`embed.py` instead of leann-core (default)**: leann-core pulls PyTorch + CUDA (~3GB), unusable on low-disk ARM devices. The default index is sqlite3 + struct float32 + pure-Python cosine. Chunking (400 words / 50 overlap) covers long CLAUDE.md files; search deduplicates to one best chunk per document.
- **`search.py` as router**: `SYNAPTEX_SEARCH_BACKEND=embed|leann|fts5` selects the backend at runtime. leann is imported lazily (no startup cost when not used). fts5 is fully offline.
- **`forge.py` multi-forge + local**: `GIT_TYPE=local` scans `LOCAL_REPOS_PATH` for `.git/` directories and reads files directly — no network, no token.
- **`mcp_cortex.py` stdio only**: Claude Code requires stdio transport for local MCP servers.
- **No `python-dotenv`**: `.env` is parsed manually in `_load_env()`. `OLLAMA_HOST` (exported by `ollama_select` in `.bashrc` if present) overrides `OLLAMA_BASE_URL`. Source `.bashrc` before calling `cortex` manually if you use `ollama_select`.
- **qmd via Bun**: `@tobilu/qmd` is TypeScript, requires Node ≥ 22 or Bun. Bun has native ARM64 binaries.

## Configuration

`~/.synaptex/.env` (chmod 600, not in repo):

```env
GIT_TYPE=forgejo              # forgejo | gitea | github | gitlab | local
GIT_URL=http://<host>:3000    # not needed for github or local
GIT_TOKEN=<read-only-token>
GIT_USER=<username>
LOCAL_REPOS_PATH=~/projects     # used when GIT_TYPE=local

SYNAPTEX_SEARCH_BACKEND=embed     # embed | leann | fts5

OLLAMA_BASE_URL=http://<ollama-host>:11434
OLLAMA_API_TYPE=ollama          # ollama | openai
OLLAMA_EMBED_MODEL=nomic-embed-text
# OLLAMA_FALLBACK_MODEL=
# OLLAMA_API_KEY=
```

## Registered MCPs (scope user)

After running `install.sh`:

- `synaptex-search` → `python3 <repo-path>/mcp_cortex.py`  
  Tools: `cortex_search`, `cortex_list`, `cortex_context`, `cortex_status`
- `qmd` → `<bun-path>/qmd mcp` (if qmd installed)  
  Episodic memory BM25 + vectors, index in `~/.cache/qmd/index.sqlite`

Slash command: `/user:synaptex [repos...]` → `~/.claude/commands/user/cortex.md`

## Search Backend Notes

- **`embed`**: requires `OLLAMA_BASE_URL` at index and query time. Best default for most setups.
- **`leann`**: install with `bash install.sh --enable-leann`. Lazy import — won't affect startup if not configured. LeannIndex API: verify with `python -c "import leann; help(leann.LeannIndex)"` after install.
- **`fts5`**: keyword-only, zero deps, zero Ollama. Populated alongside `embed` tables in the same sqlite DB. Good for fully offline machines.
