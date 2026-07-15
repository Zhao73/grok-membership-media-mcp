import inspect
import json

import pytest

from grok_membership_media_mcp import __version__
from grok_membership_media_mcp.config import Settings
from grok_membership_media_mcp.db import JobStore
from grok_membership_media_mcp.paths import sha256_file
from grok_membership_media_mcp.server import (
    _reconcile_job,
    _spawn_worker,
    _verify_completed_artifacts,
    mcp,
    start_website_video,
)


def test_mcp_initialize_version_matches_package():
    assert mcp._mcp_server.version == __version__


def test_public_tool_has_no_idempotency_bypass_or_overwrite():
    parameters = inspect.signature(start_website_video).parameters
    assert "force_new" not in parameters
    assert "overwrite" not in parameters


def test_completed_artifacts_are_rechecked(tmp_path):
    settings = Settings(
        state_dir=tmp_path / "state",
        allowed_roots=(tmp_path.resolve(),),
        grok_bin="/fake/grok",
        chatgpt_imagegen_bin="/fake/chatgpt-imagegen",
        ffmpeg_bin="/fake/ffmpeg",
        ffprobe_bin="/fake/ffprobe",
        grok_timeout_seconds=10,
        chatgpt_timeout_seconds=10,
    )
    store = JobStore(settings.database_path)
    job = store.create("website_video", {"prompt": "hello"})
    outputs = {}
    for label in ("master", "web", "poster", "first_frame"):
        path = tmp_path / f"{label}.bin"
        path.write_bytes(label.encode())
        outputs[f"{label}_path"] = str(path)
        outputs[f"{label}_sha256"] = sha256_file(path)
    manifest_path = tmp_path / "manifest.json"
    outputs["manifest_path"] = str(manifest_path)
    request = {"prompt": "hello"}
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "policy": {
                    "developer_api_used": False,
                    "api_key_auth_disabled": True,
                    "chatgpt_backend": "web_only",
                    "grok_transport": "grok_build_membership_cli",
                },
                "request": request,
                "outputs": dict(outputs),
            }
        ),
        encoding="utf-8",
    )
    outputs["manifest_sha256"] = sha256_file(manifest_path)
    completed = store.update(
        job["id"],
        status="completed",
        phase="completed",
        submission="confirmed",
        retry_safe=False,
        result_json={"outputs": outputs},
    )
    assert _verify_completed_artifacts(settings, store, completed)["status"] == "completed"

    (tmp_path / "master.bin").write_bytes(b"tampered")
    verified = _verify_completed_artifacts(settings, store, store.get(job["id"]))
    assert verified["status"] == "failed"
    assert verified["phase"] == "artifact_verification"


def test_completed_artifacts_require_hashes_and_matching_manifest(tmp_path):
    settings = Settings(
        state_dir=tmp_path / "state",
        allowed_roots=(tmp_path.resolve(),),
        grok_bin="/fake/grok",
        chatgpt_imagegen_bin="/fake/chatgpt-imagegen",
        ffmpeg_bin="/fake/ffmpeg",
        ffprobe_bin="/fake/ffprobe",
        grok_timeout_seconds=10,
        chatgpt_timeout_seconds=10,
    )
    store = JobStore(settings.database_path)
    request = {"prompt": "hello"}
    job = store.create("website_video", request)
    outputs = {}
    for label in ("master", "web", "poster", "first_frame"):
        path = tmp_path / f"{label}.bin"
        path.write_bytes(label.encode())
        outputs[f"{label}_path"] = str(path)
    manifest_path = tmp_path / "manifest.json"
    outputs["manifest_path"] = str(manifest_path)
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": job["id"],
                "policy": {
                    "developer_api_used": False,
                    "api_key_auth_disabled": True,
                    "chatgpt_backend": "web_only",
                    "grok_transport": "grok_build_membership_cli",
                },
                "request": {"prompt": "tampered"},
                "outputs": dict(outputs),
            }
        ),
        encoding="utf-8",
    )
    completed = store.update(
        job["id"],
        status="completed",
        phase="completed",
        submission="confirmed",
        retry_safe=False,
        result_json={"outputs": outputs},
    )
    verified = _verify_completed_artifacts(settings, store, completed)
    assert verified["status"] == "failed"
    assert "master_sha256" in verified["error"]


def test_reconcile_allows_worker_launch_grace(monkeypatch, tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create("website_video", {"prompt": "hello"})
    monkeypatch.setattr(
        "grok_membership_media_mcp.server.time.time",
        lambda: float(job["updated_at"]) + 5,
    )
    reconciled = _reconcile_job(store, job)
    assert reconciled["status"] == "queued"
    assert reconciled["phase"] == "queued"


def test_completed_idempotent_request_is_reused_before_file_conflict(
    monkeypatch, tmp_path
):
    settings = Settings(
        state_dir=tmp_path / "state",
        allowed_roots=(tmp_path.resolve(),),
        grok_bin="/fake/grok",
        chatgpt_imagegen_bin="/fake/chatgpt-imagegen",
        ffmpeg_bin="/fake/ffmpeg",
        ffprobe_bin="/fake/ffprobe",
        grok_timeout_seconds=10,
        chatgpt_timeout_seconds=10,
    )
    store = JobStore(settings.database_path)
    monkeypatch.setattr(
        "grok_membership_media_mcp.server._runtime", lambda: (settings, store)
    )
    monkeypatch.setattr(
        "grok_membership_media_mcp.server._spawn_worker",
        lambda _settings, _store, job_id: _store.get(job_id),
    )
    output_dir = tmp_path / "site"
    first = start_website_video(
        prompt="hero",
        output_dir=str(output_dir),
        name="hero",
        first_frame_provider="grok",
    )
    store.update(
        first["id"],
        status="completed",
        phase="completed",
        submission="confirmed",
        retry_safe=False,
    )
    output_dir.mkdir()
    (output_dir / "hero.mp4").write_bytes(b"existing")
    second = start_website_video(
        prompt="hero",
        output_dir=str(output_dir),
        name="hero",
        first_frame_provider="grok",
    )
    assert second["id"] == first["id"]
    assert second["reused"] is True


def test_worker_spawn_failure_becomes_safe_terminal_job(monkeypatch, tmp_path):
    settings = Settings(
        state_dir=tmp_path / "state",
        allowed_roots=(tmp_path.resolve(),),
        grok_bin="/fake/grok",
        chatgpt_imagegen_bin="/fake/chatgpt-imagegen",
        ffmpeg_bin="/fake/ffmpeg",
        ffprobe_bin="/fake/ffprobe",
        grok_timeout_seconds=10,
        chatgpt_timeout_seconds=10,
    )
    store = JobStore(settings.database_path)
    job = store.create("website_video", {"prompt": "hello"})

    def fail_spawn(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr("grok_membership_media_mcp.server.subprocess.Popen", fail_spawn)
    with pytest.raises(OSError):
        _spawn_worker(settings, store, job["id"])
    failed = store.get(job["id"])
    assert failed["status"] == "failed"
    assert failed["submission"] == "not_submitted"
    assert failed["retry_safe"] is True


def test_dead_unknown_worker_reconciles_to_submitted_unknown(monkeypatch, tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create("website_video", {"prompt": "hello"})
    running = store.update(
        job["id"],
        status="running",
        phase="grok_video",
        submission="unknown",
        retry_safe=False,
        pid=999999,
    )
    monkeypatch.setattr(
        "grok_membership_media_mcp.server._worker_is_alive", lambda _job: False
    )
    reconciled = _reconcile_job(store, running)
    assert reconciled["status"] == "submitted_unknown"
    assert reconciled["retry_safe"] is False
