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
    assert "pre-emit critique: P5 H5 E5 S5 R5 V5" in css
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
    assert '<source src="media/demo.mp4" type="video/mp4">' in html
    assert "muted" in html
    assert "loop" in html
    assert "playsinline" in html
    assert 'poster="media/demo-poster.jpg"' in html
    assert 'fetchpriority="high"' in html
    assert 'kind="captions"' in html
    assert "autoplay" not in html
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
    assert "showModal()" in script
    assert 'event.key === "ArrowDown"' in script
    assert 'event.key === "Escape"' not in script  # native dialog owns Escape
    assert "overflow-x: clip" in css
    assert "@media (min-width: 40rem)" in css
    assert "@media (min-width: 48rem)" in css
    assert "@media (min-width: 60rem)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
