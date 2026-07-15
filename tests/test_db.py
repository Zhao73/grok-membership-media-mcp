from pathlib import Path

import pytest

from grok_membership_media_mcp.db import (
    JobStateConflict,
    JobStore,
    OutputReservationError,
)


def test_job_store_persists_and_decodes(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    created = store.create("website_video", {"prompt": "hello"})
    assert created["status"] == "queued"
    updated = store.update(
        created["id"],
        status="completed",
        phase="completed",
        result_json={"path": "/tmp/video.mp4"},
    )
    reopened = JobStore(tmp_path / "jobs.sqlite3")
    loaded = reopened.get(created["id"])
    assert loaded["status"] == "completed"
    assert loaded["result"]["path"] == "/tmp/video.mp4"
    assert updated["retry_safe"] is True


def test_job_store_idempotency_returns_existing_job(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    first = store.create("website_video", {"prompt": "same"}, idempotency_key="abc")
    second = store.create("website_video", {"prompt": "same"}, idempotency_key="abc")
    assert second["id"] == first["id"]
    assert second["reused"] is True


def test_output_reservation_blocks_different_request(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    first = store.create(
        "website_video",
        {"prompt": "first"},
        idempotency_key="idem-1",
        output_key="/site/hero",
    )
    with pytest.raises(OutputReservationError) as caught:
        store.create(
            "website_video",
            {"prompt": "second"},
            idempotency_key="idem-2",
            output_key="/site/hero",
        )
    assert caught.value.job["id"] == first["id"]


def test_safe_pre_submit_failure_releases_keys_for_retry(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    first = store.create(
        "website_video",
        {"prompt": "same"},
        idempotency_key="idem",
        output_key="/site/hero",
    )
    store.update(
        first["id"],
        status="failed",
        phase="preflight",
        submission="not_submitted",
        retry_safe=True,
    )
    second = store.create(
        "website_video",
        {"prompt": "same"},
        idempotency_key="idem",
        output_key="/site/hero",
    )
    assert second["id"] != first["id"]
    assert second.get("reused") is None


def test_update_if_status_does_not_overwrite_terminal_job(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create("website_video", {"prompt": "hello"})
    store.update(job["id"], status="completed", phase="completed")
    with pytest.raises(JobStateConflict) as caught:
        store.update_if_status(
            job["id"], {"queued", "running"}, status="cancelled_local"
        )
    assert caught.value.job["status"] == "completed"
