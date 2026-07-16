#!/bin/zsh
set -eu

ROOT="${0:A:h:h}"
cd "$ROOT"

UV="${UV_BIN:-$HOME/.local/bin/uv}"
if [[ ! -x "$UV" ]]; then
  UV="$(command -v uv || true)"
fi
[[ -n "$UV" && -x "$UV" ]] || {
  print -u2 "uv is required: https://docs.astral.sh/uv/"
  exit 1
}

PYTHON="${PYTHON_BIN:-$HOME/.pyenv/versions/3.11.13/bin/python3.11}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3.11 || command -v python3 || true)"
fi
[[ -n "$PYTHON" && -x "$PYTHON" ]] || {
  print -u2 "Python 3.11 or newer is required"
  exit 1
}

"$UV" sync --frozen --python "$PYTHON"
chmod +x "$ROOT/scripts/run-mcp.sh"
"$ROOT/.venv/bin/python" -m pytest

# Claude Desktop child processes can be denied access to scripts under
# Documents, and a symlink does not bypass that policy because macOS checks the
# real target. Install a complete runtime outside Documents and rebuild its
# venv there so the editable package path cannot point back to the checkout.
RUNTIME="${MEMBERSHIP_MEDIA_RUNTIME_DIR:-$HOME/.local/share/grok-membership-media-mcp/runtime}"
RSYNC="${RSYNC_BIN:-/usr/bin/rsync}"
[[ -x "$RSYNC" ]] || {
  print -u2 "rsync is required to install the isolated MCP runtime"
  exit 1
}
case "$RUNTIME" in
  /*) ;;
  *)
    print -u2 "MEMBERSHIP_MEDIA_RUNTIME_DIR must be an absolute path"
    exit 1
    ;;
esac
case "$RUNTIME" in
  /|"$HOME"|"$ROOT"|"$ROOT"/*|"$HOME/Documents"|"$HOME/Documents"/*)
    print -u2 "Refusing unsafe runtime directory: $RUNTIME"
    exit 1
    ;;
esac

mkdir -p "$RUNTIME"
"$RSYNC" -a --delete "$ROOT/src/" "$RUNTIME/src/"
"$RSYNC" -a --delete "$ROOT/scripts/" "$RUNTIME/scripts/"
"$RSYNC" -a --delete "$ROOT/vendor/" "$RUNTIME/vendor/"
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$ROOT/README.md" "$RUNTIME/"
chmod +x "$RUNTIME/scripts/run-mcp.sh" "$RUNTIME/scripts/chatgpt-imagegen-web-only"

(
  cd "$RUNTIME"
  "$UV" sync --frozen --no-dev --python "$PYTHON"
)

# This must be a real wrapper file, not a symlink back into Documents.
LAUNCHER_DIR="${MEMBERSHIP_MEDIA_LAUNCHER_DIR:-$HOME/.local/bin}"
LAUNCHER="$LAUNCHER_DIR/grok-membership-media-mcp"
mkdir -p "$LAUNCHER_DIR"
install -m 755 "$ROOT/scripts/launcher.sh" "$LAUNCHER"
print "Installed MCP launcher: $LAUNCHER"
print "Installed isolated runtime: $RUNTIME"
