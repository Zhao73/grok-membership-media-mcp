import os
import hashlib
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from . import __version__
from .config import Settings, membership_env
from .db import JobStateConflict, JobStore, OutputReservationError
from .paths import resolve_allowed, safe_stem, sha256_file, sniff_image_mime
from .providers import chatgpt_membership_doctor, grok_membership_doctor


INSTRUCTIONS = """Use this server for website image-to-video and Grok video requests. It never uses developer APIs or API keys. GPT first frames use the logged-in ChatGPT browser only; video and fallback images use the logged-in Grok Build membership. Start jobs asynchronously, poll get_media_job, and use returned absolute paths. Never retry submitted_unknown jobs automatically."""

mcp = FastMCP("grok-membership-media", instructions=INSTRUCTIONS)
# mcp 1.9.x does not expose FastMCP's low-level server version in its public
# constructor. Set it explicitly so initialize reports this package's version
# instead of the SDK version.
mcp._mcp_server.version = __version__


def _runtime() -> tuple[Settings, JobStore]:
    settings = Settings.from_env()
    return settings, JobStore(settings.database_path)


def _spawn_worker(settings: Settings, store: JobStore, job_id: str) -> dict[str, Any]:
    job_dir = settings.jobs_dir / job_id
    log_path = job_dir / "worker.log"
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab", buffering=0) as log_handle:
            process = subprocess.Popen(
                [sys.executable, "-m", "grok_membership_media_mcp.worker", job_id],
                cwd=Path(__file__).resolve().parents[2],
                env=membership_env(),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except Exception as error:
        try:
            store.update_if_status(
                job_id,
                {"queued"},
                status="failed",
                phase="worker_spawn",
                submission="not_submitted",
                retry_safe=True,
                log_path=str(log_path),
                error=f"worker could not start: {error}",
            )
        except JobStateConflict:
            pass
        raise
    try:
        return store.update_if_status(
            job_id,
            {"queued", "running"},
            pid=process.pid,
            log_path=str(log_path),
        )
    except JobStateConflict as conflict:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        return conflict.job


def _planned_output_paths(
    output_dir: Path, stem: str, create_webm: bool
) -> list[Path]:
    paths = [
        output_dir / f"{stem}.mp4",
        output_dir / f"{stem}-web.mp4",
        output_dir / f"{stem}-poster.jpg",
        output_dir / f"{stem}-manifest.json",
    ]
    paths.extend(
        output_dir / f"{stem}-first-frame{suffix}"
        for suffix in (".png", ".jpg", ".jpeg", ".webp")
    )
    if create_webm:
        paths.append(output_dir / f"{stem}.webm")
    return paths


def _worker_is_alive(job: dict[str, Any]) -> bool:
    pid = job.get("pid")
    if not pid:
        return False
    completed = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    command = completed.stdout.strip()
    return bool(
        completed.returncode == 0
        and "grok_membership_media_mcp.worker" in command
        and job["id"] in command
    )


def _reconcile_job(store: JobStore, job: dict[str, Any]) -> dict[str, Any]:
    if job["status"] not in {"queued", "running"}:
        return job
    # Another MCP process can poll between INSERT/Popen and the parent writing
    # the child PID. Do not pronounce a just-created launch dead in that window.
    if not job.get("pid") and time.time() - float(job["updated_at"]) < 10:
        return job
    if _worker_is_alive(job):
        return job
    if job["submission"] == "unknown":
        status = "submitted_unknown"
        retry_safe = False
    elif job["submission"] == "confirmed":
        status = "failed"
        retry_safe = False
    else:
        status = "failed"
        retry_safe = True
    try:
        return store.update_if_status(
            job["id"],
            {"queued", "running"},
            status=status,
            phase="worker_died",
            retry_safe=retry_safe,
            error="worker process exited without a terminal job update",
        )
    except JobStateConflict as conflict:
        return conflict.job


def _verify_completed_artifacts(
    settings: Settings, store: JobStore, job: dict[str, Any]
) -> dict[str, Any]:
    if job["status"] != "completed" or not isinstance(job.get("result"), dict):
        return job
    outputs = job["result"].get("outputs") or {}
    required_pairs = (
        ("master_path", "master_sha256"),
        ("web_path", "web_sha256"),
        ("poster_path", "poster_sha256"),
        ("first_frame_path", "first_frame_sha256"),
    )
    failures: list[str] = []
    pairs = list(required_pairs)
    if outputs.get("webm_path") is not None or outputs.get("webm_sha256") is not None:
        pairs.append(("webm_path", "webm_sha256"))
    for path_key, hash_key in pairs:
        raw_path = outputs.get(path_key)
        if raw_path is None:
            failures.append(f"missing {path_key}")
            continue
        try:
            path = resolve_allowed(raw_path, settings.allowed_roots, must_exist=True)
        except (ValueError, OSError):
            failures.append(f"missing or disallowed {path_key}")
            continue
        expected_hash = outputs.get(hash_key)
        if not isinstance(expected_hash, str) or len(expected_hash) != 64:
            failures.append(f"missing or invalid {hash_key}")
        else:
            try:
                actual_hash = sha256_file(path)
            except OSError:
                failures.append(f"disappeared while hashing {path_key}")
            else:
                if actual_hash != expected_hash:
                    failures.append(f"hash mismatch for {path_key}")
    raw_manifest_path = outputs.get("manifest_path")
    if raw_manifest_path:
        try:
            manifest_path = resolve_allowed(
                raw_manifest_path, settings.allowed_roots, must_exist=True
            )
            expected_manifest_hash = outputs.get("manifest_sha256")
            if not isinstance(expected_manifest_hash, str) or len(expected_manifest_hash) != 64:
                failures.append("missing or invalid manifest_sha256")
            elif sha256_file(manifest_path) != expected_manifest_hash:
                failures.append("manifest hash mismatch")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("job_id") != job["id"]:
                failures.append("manifest job_id mismatch")
            expected_policy = {
                "developer_api_used": False,
                "api_key_auth_disabled": True,
                "chatgpt_backend": "web_only",
                "grok_transport": "grok_build_membership_cli",
            }
            if manifest.get("policy") != expected_policy:
                failures.append("manifest policy mismatch")
            if manifest.get("request") != job.get("request"):
                failures.append("manifest request mismatch")
            expected_outputs = {
                key: value
                for key, value in outputs.items()
                if key != "manifest_sha256"
            }
            if manifest.get("outputs") != expected_outputs:
                failures.append("manifest outputs mismatch")
        except (ValueError, OSError, json.JSONDecodeError):
            failures.append("manifest is missing, disallowed, or invalid")
    else:
        failures.append("missing manifest_path")
    if not failures:
        return job
    try:
        return store.update_if_status(
            job["id"],
            {"completed"},
            status="failed",
            phase="artifact_verification",
            submission="confirmed",
            retry_safe=False,
            error=json.dumps(
                {"code": "ARTIFACT_TAMPERED", "failures": failures},
                ensure_ascii=False,
            ),
        )
    except JobStateConflict as conflict:
        return conflict.job


def _public_job(job: dict[str, Any], include_log: bool = False) -> dict[str, Any]:
    public = dict(job)
    if include_log and job.get("log_path"):
        path = Path(job["log_path"])
        if path.is_file():
            public["log_tail"] = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[-50:]
    return public


@mcp.tool(
    description=(
        "Check Grok.com membership login, strict API-key disable policy, ChatGPT browser "
        "membership readiness, FFmpeg, state storage, and allowed roots. Does not generate media."
    )
)
def media_doctor() -> dict[str, Any]:
    settings, _store = _runtime()
    grok = grok_membership_doctor(settings)
    chatgpt = chatgpt_membership_doctor(settings)
    return {
        "server_version": __version__,
        "policy": {
            "developer_api_used": False,
            "public_xai_api_enabled": False,
            "api_key_auth_disabled": bool(grok.get("api_key_auth_disabled")),
            "chatgpt_backend": "web_only",
        },
        "grok": grok,
        "chatgpt_imagegen": chatgpt,
        "ffmpeg": {
            "path": settings.ffmpeg_bin,
            "detected": Path(settings.ffmpeg_bin).is_file(),
        },
        "state_dir": str(settings.state_dir),
        "allowed_roots": [str(root) for root in settings.allowed_roots],
    }


@mcp.tool(
    description=(
        "Start an asynchronous website video job using only paid ChatGPT/Grok membership "
        "sessions. GPT browser creates the first frame when available; Grok membership "
        "image_gen is the safe pre-submit fallback; Grok membership image_to_video creates "
        "the MP4. Returns immediately with a job_id."
    )
)
def start_website_video(
    prompt: str,
    output_dir: str,
    motion_prompt: str = "Slow cinematic push-in with subtle atmospheric motion; keep the subject stable.",
    name: str = "grok-website-video",
    source_image: str | None = None,
    first_frame_provider: Literal["auto", "chatgpt", "grok"] = "auto",
    duration_seconds: Literal[6, 10] = 6,
    resolution: Literal["480p", "720p"] = "720p",
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] = "16:9",
    create_webm: bool = False,
) -> dict[str, Any]:
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if len(prompt) > 12000 or len(motion_prompt) > 4000:
        raise ValueError("prompt is too long")
    settings, store = _runtime()
    resolved_output = resolve_allowed(output_dir, settings.allowed_roots)
    stem = safe_stem(name)
    resolved_source: Path | None = None
    source_sha256: str | None = None
    if source_image:
        resolved_source = resolve_allowed(
            source_image, settings.allowed_roots, must_exist=True
        )
        if sniff_image_mime(resolved_source) is None:
            raise ValueError("source_image must be PNG, JPEG, or WebP")
        source_sha256 = sha256_file(resolved_source)
    request = {
        "prompt": prompt.strip(),
        "motion_prompt": motion_prompt.strip(),
        "output_dir": str(resolved_output),
        "name": stem,
        "source_image": str(resolved_source) if resolved_source else None,
        "source_image_sha256": source_sha256,
        "first_frame_provider": first_frame_provider,
        "duration_seconds": duration_seconds,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "create_webm": create_webm,
        "policy": "membership_only_no_developer_api",
    }
    canonical = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    idempotency_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    try:
        existing_job = store.get_by_idempotency_key(idempotency_key)
    except KeyError:
        existing_job = None
    if existing_job is not None and not JobStore.can_safely_retry(existing_job):
        existing_job["reused"] = True
        return _public_job(existing_job)
    existing_outputs = [
        str(path)
        for path in _planned_output_paths(resolved_output, stem, create_webm)
        if path.exists()
    ]
    if existing_outputs:
        raise FileExistsError(
            "output files already exist; choose a new name: "
            + ", ".join(existing_outputs)
        )
    output_key = str((resolved_output / stem).resolve()).casefold()
    try:
        job = store.create(
            "website_video",
            request,
            idempotency_key=idempotency_key,
            output_key=output_key,
        )
    except OutputReservationError as error:
        raise ValueError(
            f"output name is reserved by job {error.job['id']} "
            f"with status {error.job['status']}; poll that job or choose a new name"
        ) from error
    if job.get("reused"):
        return _public_job(job)
    return _public_job(_spawn_worker(settings, store, job["id"]))


@mcp.tool(description="Return the current status and verified output paths for one media job.")
def get_media_job(job_id: str, include_log: bool = False) -> dict[str, Any]:
    settings, store = _runtime()
    job = _reconcile_job(store, store.get(job_id))
    job = _verify_completed_artifacts(settings, store, job)
    return _public_job(job, include_log=include_log)


@mcp.tool(description="List recent membership media jobs without starting any generation.")
def list_media_jobs(limit: int = 20) -> list[dict[str, Any]]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    _settings, store = _runtime()
    return [_public_job(_reconcile_job(store, job)) for job in store.list(limit)]


@mcp.tool(
    description=(
        "Stop the local worker for a queued/running job. If Grok already received the media "
        "tool call, upstream quota consumption may continue; the job is marked cancelled_local "
        "or submitted_unknown and is never auto-retried."
    )
)
def cancel_media_job(job_id: str) -> dict[str, Any]:
    _settings, store = _runtime()
    job = _reconcile_job(store, store.get(job_id))
    if job["status"] in {
        "completed",
        "failed",
        "cancelled_local",
        "submitted_unknown",
    }:
        return {"cancelled": False, "reason": "job is already terminal", "job": job}
    pid = job.get("pid")
    if pid:
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    submitted_risk = bool(
        job["submission"] == "unknown" or job["phase"].startswith("grok_")
    )
    try:
        updated = store.update_if_status(
            job_id,
            {"queued", "running"},
            status="submitted_unknown" if submitted_risk else "cancelled_local",
            phase="cancelled",
            submission="unknown" if submitted_risk else job["submission"],
            retry_safe=False,
            error="local worker cancelled; upstream cancellation is not claimed",
        )
    except JobStateConflict as conflict:
        return {
            "cancelled": False,
            "reason": "job reached a terminal state before cancellation",
            "job": _public_job(conflict.job),
        }
    return {
        "cancelled": True,
        "upstream_cancelled": False,
        "job": _public_job(updated),
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
