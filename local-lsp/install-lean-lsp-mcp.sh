#!/usr/bin/env bash
# Install lean-lsp-mcp at a pinned version into ~/.local/opt/tauceti-tools with uv, on the system
# python (/usr/bin/python3, 3.14 on this host) so the venv needs only /usr (already in the sandbox)
# plus the tools directory; no uv-managed python under ~/.local/share/uv is involved.
#
#   bin/lean-lsp-mcp -> uv-tools/lean-lsp-mcp/bin/lean-lsp-mcp
set -euo pipefail
LEAN_LSP_MCP_VERSION="${LEAN_LSP_MCP_VERSION:-0.31.0}"   # PyPI, released 2026-09-30
TOOLS="${TOOLS:-$HOME/.local/opt/tauceti-tools}"
PYTHON="${PYTHON:-/usr/bin/python3}"
mkdir -p "$TOOLS/uv-tools" "$TOOLS/bin"
UV_TOOL_DIR="$TOOLS/uv-tools" UV_TOOL_BIN_DIR="$TOOLS/bin" \
  uv tool install --python "$PYTHON" --reinstall "lean-lsp-mcp==$LEAN_LSP_MCP_VERSION"
"$TOOLS/bin/lean-lsp-mcp" --help >/dev/null 2>&1 || true
readlink -f "$TOOLS/uv-tools/lean-lsp-mcp/bin/python"
"$TOOLS/uv-tools/lean-lsp-mcp/bin/python" -c 'import lean_lsp_mcp, importlib.metadata as m; print("lean-lsp-mcp", m.version("lean-lsp-mcp"), "leanclient", m.version("leanclient"), "mcp", m.version("mcp"))'
