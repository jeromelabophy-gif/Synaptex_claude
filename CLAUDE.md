# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

synaptex is a Python CLI (Click) that centralizes `CLAUDE.md` files from all your projects, builds a semantic index via a remote or local embedding provider, and exposes everything as an MCP server to Claude Code.

Runtime state lives in `~/.synaptex/` (outside the repo). The repo contains only code.

## Development Commands

```bash
# Run the CLI from the project directory
python3 synaptex.py <command>

# Or via the installed wrapper
~/.local/bin/synaptex <command>

# Available commands
python3 synaptex.py init                  # interactive setup wizard
python3 synaptex.py status                # infrastructure status (backend, index, memory, last sync)
python3 synaptex.py sync [--dry-run] [--no-index] [--verbose]  # sync git provider → memory + index
python3 synaptex.py clean [--all|--memory|--projects]  # purge local caches
python3 synaptex.py map                   # generate ~/.synaptex/index.md (Mermaid)
python3 synaptex.py context [repos]       # injectable context block → stdout
python3 synaptex.py search "query"        # search via configured backend
```

No automated tests — validate with `sync --dry-run` then `status`.

## Architecture

### Data Flow

```
Git provider / local disk → forge.py  → ~/.synaptex/projects/<repo>/CLAUDE.md
                                   → memory.py → ~/.synaptex/memory/<repo>.md
                                   → search.py → index (embed / leann / fts5)
                                                  (embeddings via Ollama or OpenAI-compatible)
```

### Modules

| File | Role |
|---|---|
| `synaptex.py` | Click CLI, `.env` loading, orchestration |
| `forge.py` | Multi-git bridge: Forgejo/Gitea (API v1), GitHub (API v3), GitLab (API v4), Local (disk scan) |
| `embed.py` | sqlite3 vector index: 400-word chunks / 50-word overlap, `<Nf` float32, cosine similarity, OpenAI-compatible API support |
| `search.py` | Search backend router: `embed` (default) / `leann` (BM25+vector) / `fts5` (offline keyword); fallback leann→embed with warning |
| `memory.py` | Stack detection by regex, `.md` memory sheets, Mermaid wikilinks graph (wikilinks only: `[[...]]`); `.synaptex_mode` per repo (vault/git) |
| `context.py` | Aggregates index.md + memory sheets → injectable stdout block |
| `mcp_synaptex.py` | MCP stdio server (`mcp` package): 4 tools exposed to Claude Code |
| `install.sh` | Full setup: Bun + qmd, `claude mcp add`, `~/.local/bin/synaptex` wrapper |

### Key Design Decisions

- **`embed.py` instead of leann-core (default)**: leann-core pulls PyTorch + CUDA (~3GB), unusable on low-disk ARM devices. The default index is sqlite3 + struct float32 + pure-Python cosine. Chunking (400 words / 50 overlap) covers long CLAUDE.md files; search deduplicates to one best chunk per document.
- **`search.py` as router**: `SYNAPTEX_SEARCH_BACKEND=embed|leann|fts5` selects the backend at runtime. leann is imported lazily (no startup cost when not used). fts5 is fully offline. If `leann` is configured but missing, backend silently falls back to `embed` with a warning.
- **`forge.py` multi-git + local**: `FORGE_TYPE=local` scans `LOCAL_REPOS_PATH` for `.git/` directories and reads files directly — no network, no token. Hidden dirs (`.xxx`) are skipped by default unless inherited from `.git/`. Variable name in config is checked against secret patterns (not values) to avoid false positives in lists like `SYNAPTEX_EXCLUDE_DIRS=Drive-Archive,Secrets,Templates`.
- **`memory.py` wikilinks**: Dependency detection uses only explicit `[[...]]` wikilinks; textual heuristics like "depends on :" are dropped to avoid Markdown fragmentation.
- **`mcp_synaptex.py` stdio only**: Claude Code requires stdio transport for local MCP servers.
- **No `python-dotenv`**: `.env` is parsed manually in `_load_env()`. `OLLAMA_HOST` (exported by `ollama_select` in `.bashrc` if present) overrides `OLLAMA_BASE_URL`. Source `.bashrc` before calling `synaptex` manually if you use `ollama_select`.
- **qmd via Bun**: `@tobilu/qmd` is TypeScript, requires Node ≥ 22 or Bun. Bun has native ARM64 binaries.

## Configuration

`~/.synaptex/.env` (chmod 600, not in repo):

```env
FORGE_TYPE=forgejo                # forgejo | gitea | github | gitlab | local
FORGE_URL=http://<host>:3000      # not needed for github or local
FORGE_TOKEN=<read-only-token>
FORGE_USER=<username>
LOCAL_REPOS_PATH=~/projects       # used when FORGE_TYPE=local; ":" separates multiple paths

SYNAPTEX_INCLUDE_PATTERNS=CLAUDE.md,project.md  # comma-separated globs; defaults to CLAUDE.md
SYNAPTEX_EXCLUDE_DIRS=Drive-Archive,Secrets,Templates  # dirs to skip during local scan
SYNAPTEX_SEARCH_BACKEND=embed     # embed | leann | fts5

OLLAMA_BASE_URL=http://<ollama-host>:11434
OLLAMA_API_TYPE=ollama            # ollama | openai
OLLAMA_EMBED_MODEL=nomic-embed-text
# OLLAMA_FALLBACK_MODEL=
# OLLAMA_API_KEY=
```

**Notes:**
- Hidden directories (`.xxx`) are always skipped in local scan; use `SYNAPTEX_EXCLUDE_DIRS` for additional exclusions.
- `synaptex init` prompts for `SYNAPTEX_INCLUDE_PATTERNS` and `SYNAPTEX_EXCLUDE_DIRS` interactively.

## Implementation Details

### Sync Command Variants

- `synaptex sync` — full sync, re-index all docs
- `synaptex sync --dry-run` — validate patterns & repos without writing to `~/.synaptex/`
- `synaptex sync --no-index` — sync files only, skip re-indexation
- `synaptex sync --verbose` — log each file encountered with `[ok]` or `[skip]` (reason: excluded dir, hidden dir, etc.)
- `synaptex sync --exclude REPO --only REPO` — filter repos by name

Final summary line: `✓ Sync terminé : N fichiers | M docs indexés (backend) | map: synaptex map`

### Clean Command

- `synaptex clean --all` — purge `~/.synaptex/projects/` and `~/.synaptex/memory/`
- `synaptex clean --projects` — purge `projects/` only (re-index existing memory sheets)
- `synaptex clean --memory` — purge `memory/` only (rebuild Mermaid map next sync)

### Status Command

Shows:
- Forge type (forgejo, local, etc.) and local repos path or URL
- Ollama connectivity and available models
- Search backend status (embed ready / leann missing → fallback / fts5 ready)
- Index DB: document count and size in KB
- Memory sheets: count of `.md` files in `~/.synaptex/memory/`
- Projects synced: count of repos in `~/.synaptex/projects/`
- Last sync timestamp from `~/.synaptex/SYNC.log`

### Internal Changes (Breaking Changes for Development)

- **`search.rebuild_index` signature**: Now returns `tuple[int, str]` (count, backend_used) instead of `int`. Called from `synaptex.py:sync` to display backend name in summary.
- **`_local_sync` signature**: Added `verbose: bool = False` parameter to control `[skip]` / `[ok]` logging.
- **`.synaptex_mode` file**: Written per repo in `~/.synaptex/projects/<repo>/` containing `"git"` or `"vault"`. Read by `generate_index` to display badge in Mermaid map.

## Registered MCPs (scope user)

After running `install.sh`:

- `synaptex-search` → `python3 <repo-path>/mcp_synaptex.py`  
  Tools: `synaptex_search`, `synaptex_list`, `synaptex_context`, `synaptex_status`
- `qmd` → `<bun-path>/qmd mcp` (if qmd installed)  
  Episodic memory BM25 + vectors, index in `~/.cache/qmd/index.sqlite`

Slash command: `/user:synaptex [repos...]` → `~/.claude/commands/user/synaptex.md`

## Search Backend Notes

- **`embed`**: requires `OLLAMA_BASE_URL` at index and query time. Best default for most setups.
- **`leann`**: install with `bash install.sh --enable-leann`. Lazy import — won't affect startup if not configured. LeannIndex API: verify with `python -c "import leann; help(leann.LeannIndex)"` after install.
- **`fts5`**: keyword-only, zero deps, zero Ollama. Populated alongside `embed` tables in the same sqlite DB. Good for fully offline machines.
