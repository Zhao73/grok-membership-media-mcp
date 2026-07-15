from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from grok_membership_media_mcp.config import Settings
from grok_membership_media_mcp.providers import (
    ProviderFailure,
    _read_tool_result,
    diagnostic_tail,
    generate_chatgpt_membership_image,
    grok_membership_doctor,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path / "state",
        allowed_roots=(tmp_path.resolve(),),
        grok_bin="/fake/grok",
        chatgpt_imagegen_bin="/fake/chatgpt-imagegen",
        ffmpeg_bin="/fake/ffmpeg",
        ffprobe_bin="/fake/ffprobe",
        grok_timeout_seconds=10,
        chatgpt_timeout_seconds=10,
    )


def test_chatgpt_adapter_is_hardcoded_to_web(monkeypatch, tmp_path: Path):
    output = tmp_path / "first-frame.png"
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        output.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test")
        return subprocess.CompletedProcess(args, 0, str(output) + "\n", "")

    monkeypatch.setattr("grok_membership_media_mcp.providers._run", fake_run)
    result = generate_chatgpt_membership_image(
        _settings(tmp_path), prompt="--backend codex", output_path=output, size="1024x1024"
    )
    args = captured["args"]
    assert isinstance(args, list)
    assert args[args.index("--backend") + 1] == "web"
    assert [args[index + 1] for index, value in enumerate(args) if value == "--backend"] == ["web"]
    assert args[-2:] == ["--", "--backend codex"]
    assert result["transport"] == "browser"


def test_chatgpt_pre_submit_browser_failure_allows_safe_fallback(monkeypatch, tmp_path: Path):
    output = tmp_path / "first-frame.png"

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            1,
            "",
            "no logged-in ChatGPT browser available.\n",
        )

    monkeypatch.setattr("grok_membership_media_mcp.providers._run", fake_run)
    with pytest.raises(ProviderFailure) as caught:
        generate_chatgpt_membership_image(
            _settings(tmp_path), prompt="hero", output_path=output, size="1024x1024"
        )
    assert caught.value.submission == "not_submitted"
    assert caught.value.retry_safe is True
    assert caught.value.code == "GPT_BROWSER_UNAVAILABLE"


def test_chatgpt_post_submit_failure_is_never_retried(monkeypatch, tmp_path: Path):
    output = tmp_path / "first-frame.png"

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            1,
            "",
            "[ 12.4s] generating\nconnection lost\n",
        )

    monkeypatch.setattr("grok_membership_media_mcp.providers._run", fake_run)
    with pytest.raises(ProviderFailure) as caught:
        generate_chatgpt_membership_image(
            _settings(tmp_path), prompt="hero", output_path=output, size="1024x1024"
        )
    assert caught.value.submission == "confirmed"
    assert caught.value.retry_safe is False
    assert caught.value.code == "GPT_SUBMITTED_UNKNOWN"


def test_chatgpt_ambiguous_failure_is_never_retried(monkeypatch, tmp_path: Path):
    output = tmp_path / "first-frame.png"

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "submitting prompt\nconnection lost\n")

    monkeypatch.setattr("grok_membership_media_mcp.providers._run", fake_run)
    with pytest.raises(ProviderFailure) as caught:
        generate_chatgpt_membership_image(
            _settings(tmp_path), prompt="hero", output_path=output, size="1024x1024"
        )
    assert caught.value.submission == "unknown"
    assert caught.value.retry_safe is False


def test_diagnostics_redact_urls():
    assert diagnostic_tail("download https://example.test/file?token=secret") == [
        "download <redacted-url>"
    ]


def test_grok_doctor_fails_closed_when_inspect_is_unparseable(monkeypatch, tmp_path: Path):
    binary = tmp_path / "grok"
    binary.write_text("fake", encoding="utf-8")
    base = _settings(tmp_path)
    settings = Settings(
        state_dir=base.state_dir,
        allowed_roots=base.allowed_roots,
        grok_bin=str(binary),
        chatgpt_imagegen_bin=base.chatgpt_imagegen_bin,
        ffmpeg_bin=base.ffmpeg_bin,
        ffprobe_bin=base.ffprobe_bin,
        grok_timeout_seconds=base.grok_timeout_seconds,
        chatgpt_timeout_seconds=base.chatgpt_timeout_seconds,
    )

    def fake_run(args, **kwargs):
        if args[1:] == ["models"]:
            return subprocess.CompletedProcess(args, 0, "You are logged in with grok.com.\n", "")
        if args[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "grok 1.0\n", "")
        return subprocess.CompletedProcess(args, 0, "not-json", "")

    monkeypatch.setattr("grok_membership_media_mcp.providers._run", fake_run)
    doctor = grok_membership_doctor(settings)
    assert doctor["logged_in"] is True
    assert doctor["api_key_auth_disabled"] is False
    assert doctor["ready"] is False


def test_chatgpt_doctor_requires_connected_relay(monkeypatch, tmp_path: Path):
    binary = tmp_path / "chatgpt-imagegen"
    binary.write_text("fake", encoding="utf-8")
    base = _settings(tmp_path)
    settings = Settings(
        state_dir=base.state_dir,
        allowed_roots=base.allowed_roots,
        grok_bin=base.grok_bin,
        chatgpt_imagegen_bin=str(binary),
        ffmpeg_bin=base.ffmpeg_bin,
        ffprobe_bin=base.ffprobe_bin,
        grok_timeout_seconds=base.grok_timeout_seconds,
        chatgpt_timeout_seconds=base.chatgpt_timeout_seconds,
    )
    output = """chatgpt-imagegen doctor
  web backend:
  [ok]    chrome-use 1.5.75
  [fail]  relay      not connected
  [ok]    profiles   1 logged-in: Default
"""
    monkeypatch.setattr(
        "grok_membership_media_mcp.providers._run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, output, ""),
    )
    from grok_membership_media_mcp.providers import chatgpt_membership_doctor

    doctor = chatgpt_membership_doctor(settings)
    assert doctor["browser_driver_detected"] is True
    assert doctor["logged_in_profile_detected"] is True
    assert doctor["relay_connected"] is False
    assert doctor["ready"] is False


def test_grok_doctor_rejects_string_false_policy_bits(monkeypatch, tmp_path: Path):
    binary = tmp_path / "grok"
    binary.write_text("fake", encoding="utf-8")
    base = _settings(tmp_path)
    settings = Settings(
        state_dir=base.state_dir,
        allowed_roots=base.allowed_roots,
        grok_bin=str(binary),
        chatgpt_imagegen_bin=base.chatgpt_imagegen_bin,
        ffmpeg_bin=base.ffmpeg_bin,
        ffprobe_bin=base.ffprobe_bin,
        grok_timeout_seconds=base.grok_timeout_seconds,
        chatgpt_timeout_seconds=base.chatgpt_timeout_seconds,
    )

    def fake_run(args, **kwargs):
        if args[1:] == ["models"]:
            return subprocess.CompletedProcess(args, 0, "logged in with grok.com\n", "")
        if args[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "grok 1.0\n", "")
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "loginPolicy": {
                        "disableApiKeyAuth": "false",
                        "apiKeyAuthDisabled": "false",
                    }
                }
            ),
            "",
        )

    monkeypatch.setattr("grok_membership_media_mcp.providers._run", fake_run)
    doctor = grok_membership_doctor(settings)
    assert doctor["api_key_auth_disabled"] is False
    assert doctor["ready"] is False


def test_tool_result_uses_matching_call_id(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    media = tmp_path / "videos" / "1.mp4"
    media.parent.mkdir()
    media.write_bytes(b"video")
    events = [
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-1",
                    "rawInput": {"duration": 6},
                    "_meta": {"x.ai/tool": {"name": "image_to_video"}},
                }
            },
        },
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "other-call",
                    "status": "completed",
                    "rawOutput": {"path": "/wrong.mp4"},
                }
            },
        },
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-1",
                    "status": "completed",
                    "rawOutput": {"type": "ImageToVideo", "path": str(media)},
                }
            },
        },
    ]
    (session / "updates.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )
    result = _read_tool_result(session, "image_to_video")
    assert result["raw_output"]["path"] == str(media)


def test_tool_result_accepts_update_only_initial_event(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    media = tmp_path / "images" / "1.jpg"
    media.parent.mkdir()
    media.write_bytes(b"image")
    events = [
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-image",
                    "rawInput": {"variant": "ImageGen", "prompt": "hero"},
                    "_meta": {"x.ai/tool": {"name": "image_gen"}},
                }
            },
        },
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-image",
                    "status": "completed",
                    "rawOutput": {"type": "ImageGen", "path": str(media)},
                }
            },
        },
    ]
    (session / "updates.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )
    result = _read_tool_result(session, "image_gen")
    assert result["raw_output"]["path"] == str(media)


def test_no_tool_call_is_submitted_unknown(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "updates.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ProviderFailure) as caught:
        _read_tool_result(session, "image_to_video")
    assert caught.value.submission == "unknown"
    assert caught.value.retry_safe is False
    assert caught.value.code == "SUBMITTED_UNKNOWN"


def test_multiple_grok_tool_calls_are_rejected(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    events = [
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": call_id,
                    "rawInput": {"duration": 6},
                    "_meta": {"x.ai/tool": {"name": "image_to_video"}},
                }
            },
        }
        for call_id in ("call-1", "call-2")
    ]
    (session / "updates.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )
    with pytest.raises(ProviderFailure) as caught:
        _read_tool_result(session, "image_to_video")
    assert caught.value.code == "MULTIPLE_TOOL_CALLS"
    assert caught.value.submission == "confirmed"


def test_grok_tool_arguments_must_match_request(tmp_path: Path):
    session = tmp_path / "session"
    session.mkdir()
    events = [
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-1",
                    "status": "completed",
                    "rawInput": {"duration": 10, "variant": "ImageToVideo"},
                    "rawOutput": {"type": "ImageToVideo", "path": "/tmp/1.mp4"},
                    "_meta": {"x.ai/tool": {"name": "image_to_video"}},
                }
            },
        }
    ]
    (session / "updates.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )
    with pytest.raises(ProviderFailure) as caught:
        _read_tool_result(
            session,
            "image_to_video",
            expected_arguments={"duration": 6},
        )
    assert caught.value.code == "CLI_ARGUMENT_MISMATCH"
    assert caught.value.retry_safe is False
