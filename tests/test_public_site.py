from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_public_tree_contains_no_private_machine_markers() -> None:
    forbidden = (
        "/Users/",
        ".grok/sessions",
        '"session_id"',
        '"tool_call_id"',
    )
    public_files = [ROOT / "README.md", ROOT / "docs" / "PLAN.zh-CN.md"]
    public_files.extend(path for path in SITE.rglob("*") if path.is_file())

    for path in public_files:
        if path.suffix.lower() in {".mp4", ".jpg", ".jpeg", ".png", ".woff2"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{marker!r} leaked through {path.relative_to(ROOT)}"
        assert not re.search(r"\bmed_[a-f0-9]{32}\b", text), (
            f"real-looking job identifier leaked through {path.relative_to(ROOT)}"
        )


def test_deployed_tokens_match_portable_tokens() -> None:
    assert (ROOT / "tokens.css").read_bytes() == (SITE / "tokens.css").read_bytes()


def test_hallmark_contract_and_token_discipline() -> None:
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    first_line = css.splitlines()[0]
    assert first_line.startswith("/* Hallmark · macrostructure: Narrative Workflow")
    assert "genre: modern-minimal" in first_line
    assert "pre-emit critique: P5 H5 E5 S5 R5 V4" in css
    assert "transition-all" not in css
    assert "100vw" not in css
    assert "overflow-x: hidden" not in css
    assert "linear-gradient" not in css
    assert "background-clip" not in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\brgba?\(", css)
    assert "oklch(" not in css


def test_hero_video_is_real_and_accessible() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert "Website video." in html
    assert "Just ask." in html
    assert 'href="#install">Install locally</a>' in html
    assert 'href="media/demo-evidence.json">View evidence</a>' in html
    assert '<source src="media/demo.mp4" type="video/mp4">' in html
    assert "muted" in html
    assert "loop" in html
    assert "playsinline" in html
    assert 'poster="media/demo-poster.jpg"' in html
    assert 'fetchpriority="high"' in html
    assert 'kind="captions"' in html
    assert "autoplay" not in html
    assert "MemberMedia" not in html
    assert "—" not in html
    assert "–" not in html
    assert not re.search(r"\bno API\b", html, flags=re.IGNORECASE)
    assert (SITE / "media" / "demo.mp4").stat().st_size < 2_000_000


def test_public_evidence_hashes_match_assets() -> None:
    evidence = json.loads((SITE / "media" / "demo-evidence.json").read_text(encoding="utf-8"))
    assert evidence["submission"] == "confirmed"
    assert evidence["developer_api_used"] is False
    assert evidence["api_key_auth_disabled"] is True
    assert evidence["video"]["sha256"] == _sha256(SITE / "media" / "demo.mp4")
    assert evidence["poster"]["sha256"] == _sha256(SITE / "media" / "demo-poster.jpg")


def test_command_palette_and_mobile_contract_are_present() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    script = (SITE / "app.js").read_text(encoding="utf-8")
    assert '<dialog class="command-menu"' in html
    assert 'role="combobox"' in html
    assert 'role="listbox"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="command-tools"' in html
    assert "aria-activedescendant" in script
    assert 'setAttribute("aria-expanded", "true")' in script
    assert 'setAttribute("aria-expanded", "false")' in script
    assert "showModal()" in script
    assert 'event.key === "ArrowDown"' in script
    assert 'event.key === "Escape"' in script  # search inputs can otherwise consume the first Escape
    assert "overflow-x: clip" in css
    assert "@media (min-width: 40rem)" in css
    assert "@media (min-width: 48rem)" in css
    assert "@media (min-width: 60rem)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "From a plain-language request to verified files." in html
    assert "44 automated tests" in html
    for tool_name in (
        "media_doctor",
        "start_website_video",
        "get_media_job",
        "list_media_jobs",
        "cancel_media_job",
    ):
        assert tool_name in html
    for copy_target in (
        "setup-command",
        "codex-command",
        "claude-command",
        "claude-desktop-command",
    ):
        assert f'data-copy-target="{copy_target}"' in html
    assert "claude_desktop_config.json" in html
