from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


_API_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "GROK_API_KEY",
    "GROK_API_BASE_URL",
    "GROK_CODE_XAI_API_BASE_URL",
    "GROK_CODE_XAI_API_KEY",
    "GROK_XAI_API_BASE_URL",
    "GROK_XAI_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "XAI_API_BASE_URL",
    "XAI_API_KEY",
}

_COMPAT_ENV = {
    "GROK_CLAUDE_SKILLS_ENABLED": "false",
    "GROK_CLAUDE_RULES_ENABLED": "false",
    "GROK_CLAUDE_AGENTS_ENABLED": "false",
    "GROK_CLAUDE_MCPS_ENABLED": "false",
    "GROK_CLAUDE_HOOKS_ENABLED": "false",
    "GROK_CLAUDE_SESSIONS_ENABLED": "false",
    "GROK_CURSOR_SKILLS_ENABLED": "false",
    "GROK_CURSOR_RULES_ENABLED": "false",
    "GROK_CURSOR_AGENTS_ENABLED": "false",
    "GROK_CURSOR_MCPS_ENABLED": "false",
    "GROK_CURSOR_HOOKS_ENABLED": "false",
    "GROK_CURSOR_SESSIONS_ENABLED": "false",
}


def _which(name: str, fallback: str) -> str:
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    return str(Path(fallback).expanduser())


def _chatgpt_imagegen_default() -> str:
    project_root = Path(__file__).resolve().parents[2]
    wrapper = project_root / "scripts/chatgpt-imagegen-web-only"
    if wrapper.is_file():
        return str(wrapper)
    return _which("chatgpt-imagegen", "/opt/homebrew/bin/chatgpt-imagegen")


def membership_env() -> dict[str, str]:
    """Return a subprocess environment that cannot fall back to developer APIs."""
    env = dict(os.environ)
    for key in _API_ENV_KEYS:
        env.pop(key, None)
    env.update(_COMPAT_ENV)
    env.update(
        {
            "GROK_DISABLE_API_KEY_AUTH": "1",
            "GROK_MEMORY": "0",
            "GROK_SUBAGENTS": "0",
            "NO_COLOR": "1",
            "RUST_LOG": "off",
        }
    )
    # GUI apps have a minimal PATH. Make every membership CLI dependency explicit.
    path_parts = [
        str(Path.home() / ".local/bin"),
        str(Path.home() / ".npm-global/bin"),
        str(Path.home() / ".pyenv/shims"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    env["PATH"] = os.pathsep.join(path_parts)
    return env


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    allowed_roots: tuple[Path, ...]
    grok_bin: str
    chatgpt_imagegen_bin: str
    ffmpeg_bin: str
    ffprobe_bin: str
    grok_timeout_seconds: int
    chatgpt_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        state_dir = Path(
            os.environ.get(
                "MEMBERSHIP_MEDIA_STATE_DIR",
                str(Path.home() / ".local/share/grok-membership-media-mcp"),
            )
        ).expanduser()
        raw_roots = os.environ.get("MEMBERSHIP_MEDIA_ALLOWED_ROOTS", str(Path.home()))
        roots = tuple(
            Path(item).expanduser().resolve()
            for item in raw_roots.split(os.pathsep)
            if item.strip()
        )
        settings = cls(
            state_dir=state_dir,
            allowed_roots=roots,
            # Membership providers are pinned and cannot be redirected through
            # inherited GUI/MCP environment variables to an API-backed shim.
            grok_bin=str(Path.home() / ".grok/bin/grok"),
            chatgpt_imagegen_bin=_chatgpt_imagegen_default(),
            ffmpeg_bin=os.environ.get(
                "FFMPEG_BIN", _which("ffmpeg", "/opt/homebrew/bin/ffmpeg")
            ),
            ffprobe_bin=os.environ.get(
                "FFPROBE_BIN", _which("ffprobe", "/opt/homebrew/bin/ffprobe")
            ),
            grok_timeout_seconds=int(os.environ.get("MEMBERSHIP_MEDIA_GROK_TIMEOUT", "1200")),
            chatgpt_timeout_seconds=int(
                os.environ.get("MEMBERSHIP_MEDIA_CHATGPT_TIMEOUT", "420")
            ),
        )
        settings.ensure_directories()
        return settings

    @property
    def database_path(self) -> Path:
        return self.state_dir / "jobs.sqlite3"

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    def ensure_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.jobs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
