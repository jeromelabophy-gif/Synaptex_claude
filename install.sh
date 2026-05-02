#!/usr/bin/env bash
# install.sh — install Synaptex, configure MCP servers, create slash command
#
# Usage:
#   bash install.sh                # standard install (embed backend)
#   bash install.sh --enable-leann # install leann-core for BM25+vector search
set -euo pipefail

SYNAPTEX_DIR="$HOME/.synaptex"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENABLE_LEANN=0
LEANN_REQUESTED=0

for arg in "$@"; do
  [[ "$arg" == "--enable-leann" ]] && ENABLE_LEANN=1 && LEANN_REQUESTED=1
done

echo "=== Synaptex Install ==="

# Detect OS and architecture
OS="$(uname -s)"   # Linux | Darwin | MINGW64_NT-... (Windows Git Bash)
ARCH="$(uname -m)" # x86_64 | aarch64 | arm64

case "$OS" in
  Linux)   PLATFORM="linux" ;;
  Darwin)  PLATFORM="macos" ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
  *)       PLATFORM="unknown" ;;
esac

case "$ARCH" in
  x86_64)          ARCH_NORM="x86_64" ;;
  aarch64|arm64)   ARCH_NORM="aarch64" ;;
  *)               ARCH_NORM="$ARCH" ;;
esac

echo "Platform: $PLATFORM ($ARCH_NORM)"

# 1. Directories
mkdir -p "$SYNAPTEX_DIR"/{projects,memory,leann_index}
touch "$SYNAPTEX_DIR/sync.log"
echo "✓ ~/.synaptex/ created"

# 2. .env template (do not overwrite if already configured)
if [[ ! -f "$SYNAPTEX_DIR/.env" ]] || grep -q "your-token-here" "$SYNAPTEX_DIR/.env"; then
    cat > "$SYNAPTEX_DIR/.env" << 'ENVEOF'
# Forge configuration
# FORGE_TYPE: forgejo | gitea | github | gitlab | local  (default: forgejo)
FORGE_TYPE=forgejo
FORGE_URL=http://localhost:3000
FORGE_TOKEN=your-token-here
FORGE_USER=your-username
# LOCAL_REPOS_PATH: used when FORGE_TYPE=local (no API needed)
# LOCAL_REPOS_PATH=~/projects

# Search backend
# SYNAPTEX_SEARCH_BACKEND: embed (default) | leann (BM25+vector) | fts5 (offline keyword)
SYNAPTEX_SEARCH_BACKEND=embed

# Fichiers à indexer par repo (séparés par virgule)
# Exemples : CLAUDE.md  |  CLAUDE.md,README.md  |  CLAUDE.md,README.md,MEMORY.md  |  *.md
SYNAPTEX_INCLUDE_PATTERNS=CLAUDE.md

# Embedding provider (used by embed and leann backends)
# OLLAMA_BASE_URL: base URL of Ollama or any OpenAI-compatible API
OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_API_TYPE: ollama | openai  (default: ollama)
OLLAMA_API_TYPE=ollama
# OLLAMA_EMBED_MODEL: any embedding model available on your provider
OLLAMA_EMBED_MODEL=nomic-embed-text
# OLLAMA_FALLBACK_MODEL: optional second model tried if first fails
# OLLAMA_FALLBACK_MODEL=
# OLLAMA_API_KEY: required only for remote OpenAI-compatible APIs
# OLLAMA_API_KEY=
ENVEOF
    chmod 600 "$SYNAPTEX_DIR/.env"
    echo "✓ ~/.synaptex/.env created (chmod 600)"
    echo "  → Run 'synaptex init' for an interactive setup wizard"
    echo "    or edit ~/.synaptex/.env manually"
else
    echo "✓ ~/.synaptex/.env already configured"
fi

# 3. .gitignore
if [[ -f "$SCRIPT_DIR/.gitignore" ]]; then
    grep -qxF ".env" "$SCRIPT_DIR/.gitignore" || echo ".env" >> "$SCRIPT_DIR/.gitignore"
else
    echo ".env" > "$SCRIPT_DIR/.gitignore"
fi
echo "✓ .gitignore: .env protected"

# 4. Python dependencies
_pip_install() {
    local pkg="$1"
    if pip install "$pkg" 2>/dev/null; then return 0; fi
    pip install --break-system-packages "$pkg" 2>/dev/null && return 0
    echo "⚠ pip install $pkg failed (PEP 668 or network) — install manually"
    return 1
}
DEPS_OK=1
if ! python3 -c "import requests" 2>/dev/null; then
    _pip_install requests || DEPS_OK=0
fi
if ! python3 -c "import mcp" 2>/dev/null; then
    _pip_install "mcp[cli]" || DEPS_OK=0
fi
[[ $DEPS_OK -eq 1 ]] && echo "✓ Python: requests + mcp OK" || echo "⚠ Python: some deps missing — MCP server may not start"

# 4b. leann-core via uv (optional, BM25+vector search)
# uv gère sa propre version Python (3.12) — contourne PEP 668 et les wheels manquantes
if [[ $ENABLE_LEANN -eq 1 ]]; then
    UV_BIN=""
    if command -v uv &>/dev/null; then
        UV_BIN="$(command -v uv)"
    elif [[ -f "$HOME/.local/bin/uv" ]]; then
        UV_BIN="$HOME/.local/bin/uv"
    else
        echo "→ Installing uv (Python package manager)…"
        if curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null; then
            export PATH="$HOME/.local/bin:$PATH"
            UV_BIN="$HOME/.local/bin/uv"
            echo "✓ uv installed"
        else
            echo "⚠ uv install failed — leann skipped"
            echo "  Install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
            ENABLE_LEANN=0
        fi
    fi

    if [[ $ENABLE_LEANN -eq 1 && -n "$UV_BIN" ]]; then
        LEANN_VENV="$SYNAPTEX_DIR/venv"
        echo "→ Creating Python 3.12 venv via uv…"
        if ! "$UV_BIN" venv --python 3.12 "$LEANN_VENV" 2>/dev/null; then
            echo "⚠ Could not create Python 3.12 venv (uv will try to download it)"
            "$UV_BIN" venv --python 3.12 "$LEANN_VENV" || { echo "⚠ venv creation failed — leann skipped"; ENABLE_LEANN=0; }
        fi
        if [[ $ENABLE_LEANN -eq 1 ]]; then
            echo "→ Installing leann-core (~3GB, this may take a few minutes)…"
            if "$UV_BIN" pip install --python "$LEANN_VENV/bin/python" leann-core leann-backend-hnsw; then
                echo "✓ leann-core installed (Python 3.12 via uv)"
            else
                echo "⚠ leann-core install failed — continuing without it."
                echo "  Available backends: embed (Ollama) or fts5 (offline)."
                ENABLE_LEANN=0
            fi
        fi
    fi

    if [[ $ENABLE_LEANN -eq 1 ]] && [[ -f "$SYNAPTEX_DIR/.env" ]]; then
        if grep -q "^SYNAPTEX_SEARCH_BACKEND=" "$SYNAPTEX_DIR/.env"; then
            sed -i 's/^SYNAPTEX_SEARCH_BACKEND=.*/SYNAPTEX_SEARCH_BACKEND=leann/' "$SYNAPTEX_DIR/.env"
        else
            echo "SYNAPTEX_SEARCH_BACKEND=leann" >> "$SYNAPTEX_DIR/.env"
        fi
        echo "✓ SYNAPTEX_SEARCH_BACKEND=leann"
    fi
fi

if [[ $LEANN_REQUESTED -eq 1 && $ENABLE_LEANN -eq 0 ]]; then
    echo "⚠ leann skipped — continuing without it."
    ACTIVE_BACKEND=$(grep '^SYNAPTEX_SEARCH_BACKEND=' "$SYNAPTEX_DIR/.env" 2>/dev/null | cut -d= -f2 || echo embed)
    echo "  Active backend: SYNAPTEX_SEARCH_BACKEND=${ACTIVE_BACKEND}"
fi

# 5. qmd — episodic memory (optional, skip gracefully if not available)
install_qmd() {
    # Try pre-built binary first
    QMD_RELEASE=$(curl -sf --max-time 10 "https://api.github.com/repos/tobi/qmd/releases/latest" 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))" 2>/dev/null || echo "")

    PREBUILT=0
    if [[ -n "$QMD_RELEASE" ]]; then
        # Build the expected binary name pattern based on detected arch/OS
        case "$PLATFORM-$ARCH_NORM" in
          linux-aarch64)  PATTERNS=("aarch64-linux" "arm64-linux" "linux-aarch64" "linux-arm64") ;;
          linux-x86_64)   PATTERNS=("x86_64-linux" "linux-x86_64" "linux-amd64") ;;
          macos-aarch64)  PATTERNS=("aarch64-apple" "arm64-apple" "darwin-arm64" "macos-arm64") ;;
          macos-x86_64)   PATTERNS=("x86_64-apple" "darwin-x86_64" "macos-x86_64" "darwin-amd64") ;;
          *)              PATTERNS=() ;;
        esac

        ASSET_URL=""
        for pat in "${PATTERNS[@]}"; do
            ASSET_URL=$(curl -sf --max-time 10 "https://api.github.com/repos/tobi/qmd/releases/latest" \
                | python3 -c "
import sys,json
assets = json.load(sys.stdin).get('assets',[])
pat = '$pat'
urls = [a['browser_download_url'] for a in assets if pat in a['name'].lower()]
print(urls[0] if urls else '')
" 2>/dev/null || echo "")
            [[ -n "$ASSET_URL" ]] && break
        done

        if [[ -n "$ASSET_URL" ]]; then
            echo "→ Pre-built qmd binary found: $ASSET_URL"
            curl -L --max-time 60 "$ASSET_URL" -o /tmp/qmd_bin
            chmod +x /tmp/qmd_bin
            if [[ "$PLATFORM" == "windows" ]]; then
                mkdir -p "$HOME/.local/bin"
                mv /tmp/qmd_bin "$HOME/.local/bin/qmd"
            elif command -v sudo &>/dev/null; then
                sudo mv /tmp/qmd_bin /usr/local/bin/qmd
            else
                mkdir -p "$HOME/.local/bin"
                mv /tmp/qmd_bin "$HOME/.local/bin/qmd"
            fi
            PREBUILT=1
        fi
    fi

    if [[ $PREBUILT -eq 0 ]]; then
        # Fall back to Bun (cross-platform, no Node version requirement)
        if ! command -v bun &>/dev/null && [[ ! -f "$HOME/.bun/bin/bun" ]]; then
            echo "→ Installing Bun (required for qmd)…"
            curl -fsSL https://bun.sh/install | bash
        fi
        export PATH="$HOME/.bun/bin:$PATH"
        if command -v bun &>/dev/null; then
            local QMD_ERR
            QMD_ERR=$(mktemp /tmp/qmd_install_XXXXXX)
            trap "rm -f $QMD_ERR" RETURN
            if ! bun install -g @tobilu/qmd 2>"$QMD_ERR"; then
                if grep -q "node-gyp\|better-sqlite3" "$QMD_ERR" 2>/dev/null; then
                    echo "⚠ qmd: dépendance native manquante (node-gyp). Synaptex works without qmd."
                else
                    echo "⚠ qmd install échoué. Synaptex works without qmd."
                    cat "$QMD_ERR" >&2
                fi
                return 1
            fi
        else
            echo "⚠ Bun not available — skipping qmd install"
            echo "  Install manually: https://bun.sh → bun install -g @tobilu/qmd"
            return 1
        fi
    fi
    return 0
}

if command -v qmd &>/dev/null || [[ -f "$HOME/.bun/bin/qmd" ]]; then
    echo "✓ qmd already installed"
else
    echo "→ Installing qmd (episodic memory)…"
    if install_qmd; then
        echo "✓ qmd installed"
    else
        echo "⚠ qmd not installed — synaptex works without it (MCP qmd will be skipped)"
    fi
fi

# 6. MCP registration (only if Claude Code CLI is installed)
if ! command -v claude &>/dev/null; then
    echo "⚠ Claude Code CLI not found — skipping MCP registration"
    echo ""
    echo "┌─ Action required ────────────────────────────────────────────────────────────┐"
    echo "│  Once Claude Code is installed, run:                                         │"
    echo "│    claude mcp add --scope user synaptex-search -- python3 $SCRIPT_DIR/mcp_synaptex.py"
    echo "│                                                                               │"
    echo "│  Install Claude Code: https://claude.com/download                            │"
    echo "└───────────────────────────────────────────────────────────────────────────────┘"
else
    # 6a. MCP synaptex-search (semantic search)
    if claude mcp list 2>/dev/null | grep -q "synaptex-search"; then
        echo "✓ MCP synaptex-search already configured"
    else
        if claude mcp add --scope user synaptex-search -- python3 "$SCRIPT_DIR/mcp_synaptex.py" 2>/dev/null; then
            echo "✓ MCP synaptex-search configured"
        else
            echo "⚠ Failed to register MCP synaptex-search (continuing)"
        fi
    fi

    # 6b. MCP qmd (episodic memory) — only if qmd is available
    QMD_BIN=""
    if command -v qmd &>/dev/null; then
        QMD_BIN="$(command -v qmd)"
    elif [[ -f "$HOME/.bun/bin/qmd" ]]; then
        QMD_BIN="$HOME/.bun/bin/qmd"
    elif [[ -f "$HOME/.local/bin/qmd" ]]; then
        QMD_BIN="$HOME/.local/bin/qmd"
    fi

    if [[ -n "$QMD_BIN" ]]; then
        if claude mcp list 2>/dev/null | grep -q "^qmd"; then
            echo "✓ MCP qmd already configured"
        else
            if claude mcp add --scope user qmd -- "$QMD_BIN" mcp 2>/dev/null; then
                echo "✓ MCP qmd configured"
            else
                echo "⚠ Failed to register MCP qmd (continuing)"
            fi
        fi
    else
        echo "⚠ qmd not found — MCP qmd not registered (optional)"
    fi
fi

# 7. Slash command synaptex
SLASH_DIR="$HOME/.claude/commands/user"
mkdir -p "$SLASH_DIR"
cat > "$SLASH_DIR/synaptex.md" << 'SLASHEOF'
Load the Synaptex brain for this session.

Steps:
1. Run `synaptex context $ARGUMENTS` and read the result
2. MCP qmd is available: query relevant memories from past sessions
3. For cross-project semantic search: run `synaptex search "<query>"`
4. Confirm: "🧠 Synaptex loaded — active projects: [list]"
SLASHEOF
echo "✓ Slash command created: ~/.claude/commands/user/synaptex.md"

# 8. Make synaptex.py executable and accessible
chmod +x "$SCRIPT_DIR/synaptex.py"

if [[ "$PLATFORM" != "windows" ]]; then
    SYNAPTEX_BIN="$HOME/.local/bin/synaptex"
    mkdir -p "$HOME/.local/bin"
    cat > "$SYNAPTEX_BIN" << BINEOF
#!/usr/bin/env bash
# Use ~/.synaptex/venv if leann was installed there
if [[ -f "$SYNAPTEX_DIR/venv/bin/python3" ]]; then
    exec "$SYNAPTEX_DIR/venv/bin/python3" "$SCRIPT_DIR/synaptex.py" "\$@"
else
    exec python3 "$SCRIPT_DIR/synaptex.py" "\$@"
fi
BINEOF
    chmod +x "$SYNAPTEX_BIN"
    echo "✓ synaptex available in ~/.local/bin/synaptex"
    echo "  To make it permanent:"
    if [[ "$SHELL" == *fish* ]]; then
        echo "    Fish: add to ~/.config/fish/config.fish :"
        echo "      fish_add_path \$HOME/.local/bin"
    else
        echo "    bash/zsh: add to ~/.bashrc or ~/.zshrc:"
        echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
        echo "    Then: source ~/.bashrc   (or source ~/.zshrc)"
        if command -v fish &>/dev/null; then
            echo "    Fish (detected): add to ~/.config/fish/config.fish :"
            echo "      fish_add_path \$HOME/.local/bin"
        fi
    fi
else
    echo "⚠ Windows detected — add the following alias manually:"
    echo "  alias synaptex='python3 $SCRIPT_DIR/synaptex.py'"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Run 'synaptex init'       — interactive setup wizard"
echo "     or edit ~/.synaptex/.env  — fill in FORGE_TYPE, FORGE_URL, token"
echo "  2. synaptex status           — verify connectivity"
echo "  3. synaptex sync --dry-run   — preview sync"
echo "  4. synaptex sync             — full sync + index"
echo "  5. /user:synaptex            — in Claude Code"
echo ""
echo "Search backends:"
echo "  Default  : SYNAPTEX_SEARCH_BACKEND=embed  (semantic, requires Ollama)"
echo "  Offline  : SYNAPTEX_SEARCH_BACKEND=fts5   (keyword, no Ollama needed)"
echo "  BM25+vec : leann-core — best quality, but ~3GB install"
echo ""
echo "  To enable leann, run ONE of:"
echo "    bash $SCRIPT_DIR/install.sh --enable-leann   # automatic install + config"
echo ""
echo "    or manually (requires uv: curl -LsSf https://astral.sh/uv/install.sh | sh):"
echo "    uv venv --python 3.12 ~/.synaptex/venv"
echo "    uv pip install --python ~/.synaptex/venv/bin/python leann-core leann-backend-hnsw"
echo "    # then set SYNAPTEX_SEARCH_BACKEND=leann in ~/.synaptex/.env"
