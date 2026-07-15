from pathlib import Path

import pytest

from grok_membership_media_mcp.paths import PathPolicyError, resolve_allowed, safe_stem


def test_resolve_allowed_accepts_descendant(tmp_path: Path):
    output = resolve_allowed(tmp_path / "site" / "media", (tmp_path.resolve(),))
    assert output == (tmp_path / "site" / "media").resolve()


def test_resolve_allowed_rejects_escape(tmp_path: Path):
    with pytest.raises(PathPolicyError):
        resolve_allowed(Path("/tmp/outside-membership-media"), (tmp_path.resolve(),))


def test_safe_stem_removes_shell_and_path_characters():
    assert safe_stem("../../ Hero $(touch bad) ") == "Hero-touch-bad"
