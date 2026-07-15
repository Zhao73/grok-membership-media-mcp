from grok_membership_media_mcp.config import membership_env


def test_web_only_wrapper_rejects_codex_backend():
    import subprocess
    from pathlib import Path

    wrapper = Path(__file__).resolve().parents[1] / "scripts/chatgpt-imagegen-web-only"
    completed = subprocess.run(
        [str(wrapper), "--backend", "codex", "--", "must fail"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert completed.returncode == 64
    assert "exactly --backend web is required" in completed.stderr


def test_membership_env_forbids_developer_api_keys(monkeypatch):
    for key in (
        "XAI_API_KEY",
        "XAI_API_BASE_URL",
        "GROK_API_KEY",
        "GROK_XAI_API_KEY",
        "GROK_CODE_XAI_API_KEY",
        "GROK_CODE_XAI_API_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.setenv(key, "must-not-leak")
    env = membership_env()
    for key in (
        "XAI_API_KEY",
        "XAI_API_BASE_URL",
        "GROK_API_KEY",
        "GROK_XAI_API_KEY",
        "GROK_CODE_XAI_API_KEY",
        "GROK_CODE_XAI_API_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        assert key not in env
    assert env["GROK_DISABLE_API_KEY_AUTH"] == "1"
    assert env["GROK_CLAUDE_MCPS_ENABLED"] == "false"


def test_source_contains_no_direct_xai_rest_adapter():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src"
    source = "\n".join(path.read_text() for path in root.rglob("*.py"))
    assert "api.x.ai" not in source
    assert "/v1/videos" not in source
    assert "/v1/images" not in source
    assert "import requests" not in source
    assert "urllib.request" not in source
