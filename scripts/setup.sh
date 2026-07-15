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
