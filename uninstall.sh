#!/usr/bin/env bash
# uninstall.sh — remove Synaptex completely and reset to a clean state
#
# Usage:
#   bash uninstall.sh             # remove everything (asks confirmation)
#   bash uninstall.sh --keep-data # keep ~/.synaptex/ (useful before reinstall)
#   bash uninstall.sh --yes       # non-interactive (for scripts/CI)
set -euo pipefail

SYNAPTEX_DIR="$HOME/.synaptex"
KEEP_DATA=0
YES=0

for arg in "$@"; do
    [[ "$arg" == "--keep-data" ]] && KEEP_DATA=1
    [[ "$arg" == "--yes" ]]       && YES=1
done

echo "=== Synaptex Uninstall ==="
echo ""

if [[ $YES -eq 0 ]]; then
    if [[ $KEEP_DATA -eq 1 ]]; then
        echo "This will remove:"
        echo "  • ~/.local/bin/synaptex"
        echo "  • ~/.claude/commands/user/synaptex.md"
        echo "  • MCP servers: synaptex-search, qmd"
        echo "  • qmd binary (if installed by Synaptex)"
        echo ""
        echo "This will KEEP:"
        echo "  • ~/.synaptex/ (data, index, .env)"
    else
        echo "This will remove:"
        echo "  • ~/.synaptex/  (all data, index, .env, venv)"
        echo "  • ~/.local/bin/synaptex"
        echo "  • ~/.claude/commands/user/synaptex.md"
        echo "  • MCP servers: synaptex-search, qmd"
        echo "  • qmd binary (if installed by Synaptex)"
    fi
    echo ""
    read -rp "Continue? [y/N] " REPLY
    if [[ "${REPLY,,}" != "y" && "${REPLY,,}" != "yes" ]]; then
        echo "Aborted."
        exit 0
    fi
    echo ""
fi

# 1. MCP registrations
if command -v claude &>/dev/null; then
    if claude mcp list 2>/dev/null | grep -q "synaptex-search"; then
        claude mcp remove --scope user synaptex-search 2>/dev/null && echo "✓ MCP synaptex-search removed" \
            || echo "⚠ Could not remove MCP synaptex-search"
    else
        echo "  MCP synaptex-search: not registered"
    fi

    if claude mcp list 2>/dev/null | grep -q "^qmd"; then
        claude mcp remove --scope user qmd 2>/dev/null && echo "✓ MCP qmd removed" \
            || echo "⚠ Could not remove MCP qmd"
    else
        echo "  MCP qmd: not registered"
    fi
else
    echo "  Claude Code CLI not found — skipping MCP cleanup"
fi

# 2. Slash command
SLASH_CMD="$HOME/.claude/commands/user/synaptex.md"
if [[ -f "$SLASH_CMD" ]]; then
    rm -f "$SLASH_CMD"
    echo "✓ Slash command removed: $SLASH_CMD"
else
    echo "  Slash command: not found"
fi

# 3. synaptex wrapper binary
SYNAPTEX_BIN="$HOME/.local/bin/synaptex"
if [[ -f "$SYNAPTEX_BIN" ]]; then
    rm -f "$SYNAPTEX_BIN"
    echo "✓ Wrapper removed: $SYNAPTEX_BIN"
else
    echo "  Wrapper: not found"
fi

# 4. qmd binary — only remove from locations where install.sh puts it
for QMD_PATH in "/usr/local/bin/qmd" "$HOME/.local/bin/qmd" "$HOME/.bun/bin/qmd"; do
    if [[ -f "$QMD_PATH" ]]; then
        if [[ "$QMD_PATH" == /usr/local/bin/* ]]; then
            sudo rm -f "$QMD_PATH" 2>/dev/null && echo "✓ qmd removed: $QMD_PATH" \
                || echo "⚠ Could not remove $QMD_PATH (try: sudo rm $QMD_PATH)"
        else
            rm -f "$QMD_PATH" && echo "✓ qmd removed: $QMD_PATH"
        fi
    fi
done

# 5. ~/.synaptex/ data directory
if [[ $KEEP_DATA -eq 0 ]]; then
    if [[ -d "$SYNAPTEX_DIR" ]]; then
        rm -rf "$SYNAPTEX_DIR"
        echo "✓ Data directory removed: $SYNAPTEX_DIR"
    else
        echo "  Data directory: not found"
    fi
else
    echo "  Data directory kept: $SYNAPTEX_DIR"
fi

echo ""
echo "=== Synaptex uninstalled ==="
if [[ $KEEP_DATA -eq 1 ]]; then
    echo ""
    echo "Your data in ~/.synaptex/ is intact."
    echo "To reinstall: bash install.sh"
fi
