#!/bin/zsh
set -eu

RUNTIME="${MEMBERSHIP_MEDIA_RUNTIME_DIR:-$HOME/.local/share/grok-membership-media-mcp/runtime}"
SERVER="$RUNTIME/scripts/run-mcp.sh"

if [[ ! -x "$SERVER" ]]; then
  print -u2 "grok-membership-media-mcp runtime is not installed. Run scripts/setup.sh from the repository."
  exit 1
fi

exec "$SERVER"
