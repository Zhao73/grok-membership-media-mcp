#!/bin/zsh
set -eu

ROOT="${0:A:h:h}"
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$HOME/.pyenv/shims:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export GROK_BIN="$HOME/.grok/bin/grok"
export CHATGPT_IMAGEGEN_BIN="$ROOT/scripts/chatgpt-imagegen-web-only"
export MEMBERSHIP_MEDIA_ALLOWED_ROOTS="${MEMBERSHIP_MEDIA_ALLOWED_ROOTS:-$HOME}"
export GROK_DISABLE_API_KEY_AUTH=1
unset XAI_API_KEY XAI_API_BASE_URL GROK_XAI_API_KEY GROK_XAI_API_BASE_URL GROK_API_KEY GROK_API_BASE_URL GROK_CODE_XAI_API_KEY GROK_CODE_XAI_API_BASE_URL OPENAI_API_KEY OPENAI_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_BASE_URL || true

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  print -u2 "grok-membership-media-mcp is not installed. Run: $ROOT/scripts/setup.sh"
  exit 1
fi

exec "$PYTHON" -m grok_membership_media_mcp.server
