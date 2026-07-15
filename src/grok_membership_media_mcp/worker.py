from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Settings, membership_env
from .db import JobStateConflict, JobStore
from .paths import atomic_copy, resolve_allowed, safe_stem, sha256_file, sniff_image_mime
from .providers import (
    ProviderFailure,
    chatgpt_membership_doctor,
    generate_chatgpt_membership_image,
    grok_membership_doctor,
    run_grok_membership_tool,
    terminate_active_process,
)


LOGGER = logging.getLogger("grok-membership-media-worker")
_CURRENT_JOB_ID: str | None = None
_STORE: JobStore | None = None


def _signal_handler(signum: int, _frame: object) -> None:
    terminate_active_process()
    if _STORE is not None and _CURRENT_JOB_ID is not None:
        try:
            job = _STORE.get(_CURRENT_JOB_ID)
            submitted_risk = bool(
                job["submission"] == "unknown" or job["phase"].startswith("grok_")
            )
            _STORE.update_if_status(
                _CURRENT_JOB_ID,
                {"queued", "running"},
                status="submitted_unknown" if submitted_risk else "cancelled_local",
                phase="cancelled",
                submission="unknown" if submitted_risk else job["submission"],
                retry_safe=False,
                error=f"worker stopped by signal {signum}",
            )
        except (JobStateConflict, KeyError):
            pass
    raise SystemExit(128 + signum)


def _size_for_aspect_ratio(aspect_ratio: str) -> str:
    return {
        "16:9": "1536x864",
        "9:16": "864x1536",
        "1:1": "1024x1024",
        "4:3": "1365x1024",
        "3:4": "1024x1365",
    }.get(aspect_ratio, "auto")


_TARGET_DIMENSIONS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (1024, 1024),
    "4:3": (1280, 960),
    "3:4": (960, 1280),
}


def _probe_media(settings: Settings, path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            settings.ffprobe_bin,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        env=membership_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise ProviderFailure(
            "ffprobe rejected the generated video",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
            diagnostics=completed.stderr.splitlines()[-20:],
        )
    payload = json.loads(completed.stdout)
    return payload


def _main_video_stream(payload: dict[str, Any]) -> dict[str, Any]:
    video_streams = [
        item
        for item in payload.get("streams", [])
        if item.get("codec_type") == "video"
        and not (item.get("disposition") or {}).get("attached_pic")
    ]
    if not video_streams:
        raise ProviderFailure(
            "generated media has no decodable non-attached video stream",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    return video_streams[0]


def _decode_video(settings: Settings, path: Path, stream_index: int) -> None:
    completed = subprocess.run(
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            f"0:{stream_index}",
            "-f",
            "null",
            "-",
        ],
        env=membership_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        raise ProviderFailure(
            "generated video failed a full decode pass",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
            diagnostics=completed.stderr.splitlines()[-20:],
        )


def _validate_video(
    settings: Settings,
    path: Path,
    *,
    expected_duration: int,
    expected_resolution: str,
    expected_aspect_ratio: str,
    require_h264: bool = False,
    require_no_audio: bool = False,
) -> dict[str, Any]:
    payload = _probe_media(settings, path)
    stream = _main_video_stream(payload)
    codec = stream.get("codec_name")
    if require_h264 and codec != "h264":
        raise ProviderFailure(
            f"website MP4 codec is {codec!r}, expected H.264",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ProviderFailure(
            "generated video has invalid dimensions",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    expected_width, expected_height = _TARGET_DIMENSIONS[expected_aspect_ratio]
    expected_ratio = expected_width / expected_height
    if abs((width / height) - expected_ratio) / expected_ratio > 0.06:
        raise ProviderFailure(
            f"generated video aspect ratio {width}x{height} does not match {expected_aspect_ratio}",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    minimum_short_side = 700 if expected_resolution == "720p" else 480
    if min(width, height) < minimum_short_side:
        raise ProviderFailure(
            f"generated video resolution {width}x{height} is below {expected_resolution}",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    try:
        duration = float((payload.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if abs(duration - expected_duration) > 1.5:
        raise ProviderFailure(
            f"generated video duration {duration:.3f}s does not match {expected_duration}s",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    if require_no_audio and any(
        item.get("codec_type") == "audio" for item in payload.get("streams", [])
    ):
        raise ProviderFailure(
            "website MP4 unexpectedly contains audio",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    if require_h264 and stream.get("pix_fmt") != "yuv420p":
        raise ProviderFailure(
            f"website MP4 pixel format is {stream.get('pix_fmt')!r}, expected yuv420p",
            phase="video_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    _decode_video(settings, path, int(stream["index"]))
    return payload


def _run_ffmpeg(settings: Settings, args: list[str]) -> None:
    completed = subprocess.run(
        [settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y", *args],
        env=membership_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffmpeg failed")


def _image_dimensions(settings: Settings, path: Path) -> tuple[int, int]:
    payload = _probe_media(settings, path)
    streams = [
        item for item in payload.get("streams", []) if item.get("codec_type") == "video"
    ]
    if not streams:
        raise ProviderFailure(
            "first frame has no decodable image stream",
            phase="image_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    width = int(streams[0].get("width") or 0)
    height = int(streams[0].get("height") or 0)
    if width <= 0 or height <= 0:
        raise ProviderFailure(
            "first frame has invalid dimensions",
            phase="image_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    return width, height


def _normalize_first_frame(
    settings: Settings,
    *,
    source: Path,
    destination: Path,
    aspect_ratio: str,
) -> dict[str, Any]:
    source_width, source_height = _image_dimensions(settings, source)
    target_width, target_height = _TARGET_DIMENSIONS[aspect_ratio]
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        settings,
        [
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-vf",
            (
                f"scale={target_width}:{target_height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},setsar=1"
            ),
            "-frames:v",
            "1",
            str(destination),
        ],
    )
    normalized_width, normalized_height = _image_dimensions(settings, destination)
    if (normalized_width, normalized_height) != (target_width, target_height):
        raise ProviderFailure(
            "normalized first frame has unexpected dimensions",
            phase="image_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    if sniff_image_mime(destination) != "image/png":
        raise ProviderFailure(
            "normalized first frame is not a valid PNG",
            phase="image_validation",
            submission="confirmed",
            retry_safe=False,
            code="INVALID_MEDIA_RESULT",
        )
    return {
        "source_dimensions": [source_width, source_height],
        "dimensions": [normalized_width, normalized_height],
        "path": str(destination),
        "sha256": sha256_file(destination),
    }


def _postprocess(
    settings: Settings,
    *,
    source_video: Path,
    staging_dir: Path,
    name: str,
    create_webm: bool,
    source_stream_index: int,
) -> dict[str, Path]:
    stem = safe_stem(name)
    master_path = staging_dir / f"{stem}.mp4"
    web_path = staging_dir / f"{stem}-web.mp4"
    poster_path = staging_dir / f"{stem}-poster.jpg"
    webm_path = staging_dir / f"{stem}.webm"

    atomic_copy(source_video, master_path)
    _run_ffmpeg(
        settings,
        [
            "-i",
            str(master_path),
            "-map",
            f"0:{source_stream_index}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            str(web_path),
        ],
    )
    _run_ffmpeg(
        settings,
        [
            "-ss",
            "0",
            "-i",
            str(master_path),
            "-map",
            f"0:{source_stream_index}",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(poster_path),
        ],
    )
    result: dict[str, Path] = {
        "master_path": master_path,
        "web_path": web_path,
        "poster_path": poster_path,
    }
    if create_webm:
        _run_ffmpeg(
            settings,
            [
                "-i",
                str(master_path),
                "-map",
                f"0:{source_stream_index}",
                "-c:v",
                "libvpx-vp9",
                "-crf",
                "32",
                "-b:v",
                "0",
                "-an",
                str(webm_path),
            ],
        )
        result["webm_path"] = webm_path
    return result


def _move_no_replace(source: Path, destination: Path) -> None:
    """Move a staged regular file without an overwrite race.

    Staging is deliberately inside the destination directory, so hard-linking
    is same-filesystem and atomic with respect to destination creation.
    """
    os.link(source, destination)
    try:
        source.unlink()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _publish_bundle(files: dict[Path, Path]) -> None:
    existing = [str(destination) for destination in files.values() if destination.exists()]
    if existing:
        raise FileExistsError(
            "output appeared after reservation; refusing to overwrite: "
            + ", ".join(existing)
        )
    moved: list[tuple[Path, Path]] = []
    try:
        for source, destination in files.items():
            _move_no_replace(source, destination)
            moved.append((source, destination))
    except BaseException:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                try:
                    os.link(destination, source)
                    destination.unlink()
                except OSError:
                    LOGGER.exception(
                        "could not roll back partially published file %s", destination
                    )
        raise


def execute_job(job_id: str) -> None:
    global _CURRENT_JOB_ID, _STORE
    settings = Settings.from_env()
    store = JobStore(settings.database_path)
    _CURRENT_JOB_ID = job_id
    _STORE = store
    staging_dir: Path | None = None
    try:
        job = store.get(job_id)
        request = job["request"]
        store.update_if_status(
            job_id,
            {"queued"},
            status="running",
            phase="preflight",
            submission="not_submitted",
            retry_safe=True,
            # The child records its own PID so a parent MCP crash between
            # Popen and parent-side bookkeeping cannot orphan a live job.
            pid=os.getpid(),
        )
        job_dir = settings.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        output_dir = resolve_allowed(request["output_dir"], settings.allowed_roots)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_stem(request["name"])

        grok_doctor = grok_membership_doctor(settings)
        if not grok_doctor.get("ready"):
            raise ProviderFailure(
                "Grok membership/API-key-disable preflight is not ready",
                phase="preflight",
                submission="not_submitted",
                retry_safe=True,
                code=(
                    "AUTH_REQUIRED"
                    if not grok_doctor.get("logged_in")
                    else "API_KEY_POLICY_UNVERIFIED"
                ),
                diagnostics=[json.dumps(grok_doctor, ensure_ascii=False)],
            )

        source_image: Path | None = None
        first_frame: dict[str, Any] | None = None
        if request.get("source_image"):
            original_source_image = resolve_allowed(
                request["source_image"], settings.allowed_roots, must_exist=True
            )
            if sniff_image_mime(original_source_image) is None:
                raise ProviderFailure(
                    "source_image is not PNG, JPEG, or WebP",
                    phase="preflight",
                    submission="not_submitted",
                    retry_safe=True,
                    code="INVALID_MEDIA_INPUT",
                )
            current_source_sha = sha256_file(original_source_image)
            expected_source_sha = request.get("source_image_sha256")
            if expected_source_sha and current_source_sha != expected_source_sha:
                raise ProviderFailure(
                    "source_image changed after the job was queued",
                    phase="preflight",
                    submission="not_submitted",
                    retry_safe=True,
                    code="INVALID_MEDIA_INPUT",
                )
            suffix = original_source_image.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                suffix = ".image"
            source_snapshot = job_dir / f"source-image-snapshot{suffix}"
            atomic_copy(original_source_image, source_snapshot)
            snapshot_sha = sha256_file(source_snapshot)
            if snapshot_sha != current_source_sha:
                raise ProviderFailure(
                    "source_image changed while its immutable job snapshot was created",
                    phase="preflight",
                    submission="not_submitted",
                    retry_safe=True,
                    code="INVALID_MEDIA_INPUT",
                )
            source_image = source_snapshot
            first_frame = {
                "provider": "existing",
                "path": str(source_snapshot),
                "source_path": str(original_source_image),
                "sha256": snapshot_sha,
            }

        provider = request["first_frame_provider"]
        if source_image is None and provider in {"auto", "chatgpt"}:
            store.update_if_status(
                job_id,
                {"running"},
                phase="gpt_first_frame",
                submission="not_submitted",
                retry_safe=True,
            )
            doctor = chatgpt_membership_doctor(settings)
            if doctor.get("ready"):
                try:
                    gpt_path = job_dir / "first-frame-gpt.png"
                    first_frame_prompt = (
                        request["prompt"]
                        + "\nCreate a single cinematic website-video first frame. "
                        "No text, captions, logos, UI, borders, collage, or watermark. "
                        "Keep the composition simple and animation-friendly."
                    )
                    first_frame = generate_chatgpt_membership_image(
                        settings,
                        prompt=first_frame_prompt,
                        output_path=gpt_path,
                        size=_size_for_aspect_ratio(request["aspect_ratio"]),
                    )
                    source_image = gpt_path
                except ProviderFailure as error:
                    if provider == "chatgpt" or not error.retry_safe:
                        raise
                    LOGGER.warning("GPT pre-submit failure; using Grok membership image_gen")
            elif provider == "chatgpt":
                raise ProviderFailure(
                    "ChatGPT web membership backend is not ready",
                    phase="preflight",
                    submission="not_submitted",
                    retry_safe=True,
                    code="GPT_BROWSER_UNAVAILABLE",
                )

        if source_image is None:
            store.update_if_status(
                job_id,
                {"running"},
                phase="grok_first_frame",
                submission="unknown",
                retry_safe=False,
            )
            grok_image = run_grok_membership_tool(
                settings,
                tool_name="image_gen",
                arguments={
                    "prompt": (
                        request["prompt"]
                        + "\nSingle cinematic website-video first frame, simple animation-friendly composition, "
                        "no text, captions, logo, UI, border, collage, or watermark."
                    ),
                    "aspect_ratio": request["aspect_ratio"],
                },
                cwd=job_dir,
            )
            source_image = Path(grok_image["path"])
            first_frame = grok_image

        assert source_image is not None
        assert first_frame is not None
        normalized_path = job_dir / "first-frame-normalized.png"
        normalization = _normalize_first_frame(
            settings,
            source=source_image,
            destination=normalized_path,
            aspect_ratio=request["aspect_ratio"],
        )
        first_frame = {
            **first_frame,
            "original_path": first_frame.get("path"),
            "original_sha256": first_frame.get("sha256"),
            "path": normalization["path"],
            "sha256": normalization["sha256"],
            "mime_type": "image/png",
            "normalized": True,
            "source_dimensions": normalization["source_dimensions"],
            "dimensions": normalization["dimensions"],
        }
        source_image = normalized_path

        store.update_if_status(
            job_id,
            {"running"},
            phase="grok_video",
            submission="unknown",
            retry_safe=False,
        )
        video = run_grok_membership_tool(
            settings,
            tool_name="image_to_video",
            arguments={
                "image": str(source_image),
                "prompt": request["motion_prompt"],
                "duration": request["duration_seconds"],
                "resolution_name": request["resolution"],
            },
            cwd=job_dir,
        )
        source_video = Path(video["path"])
        store.update_if_status(
            job_id,
            {"running"},
            phase="postprocessing",
            submission="confirmed",
            retry_safe=False,
        )
        source_probe = _validate_video(
            settings,
            source_video,
            expected_duration=request["duration_seconds"],
            expected_resolution=request["resolution"],
            expected_aspect_ratio=request["aspect_ratio"],
        )
        source_stream = _main_video_stream(source_probe)

        staging_dir = output_dir / f".{stem}.{job_id}.staging"
        staging_dir.mkdir(mode=0o700)
        staged_outputs = _postprocess(
            settings,
            source_video=source_video,
            staging_dir=staging_dir,
            name=stem,
            create_webm=request["create_webm"],
            source_stream_index=int(source_stream["index"]),
        )
        web_probe = _validate_video(
            settings,
            staged_outputs["web_path"],
            expected_duration=request["duration_seconds"],
            expected_resolution=request["resolution"],
            expected_aspect_ratio=request["aspect_ratio"],
            require_h264=True,
            require_no_audio=True,
        )
        webm_probe: dict[str, Any] | None = None
        if "webm_path" in staged_outputs:
            webm_probe = _validate_video(
                settings,
                staged_outputs["webm_path"],
                expected_duration=request["duration_seconds"],
                expected_resolution=request["resolution"],
                expected_aspect_ratio=request["aspect_ratio"],
                require_no_audio=True,
            )
        if sniff_image_mime(staged_outputs["poster_path"]) != "image/jpeg":
            raise ProviderFailure(
                "generated poster is not a valid JPEG",
                phase="postprocessing",
                submission="confirmed",
                retry_safe=False,
                code="INVALID_MEDIA_RESULT",
            )

        staged_first_frame = staging_dir / f"{stem}-first-frame.png"
        atomic_copy(source_image, staged_first_frame)
        final_paths = {
            key: output_dir / path.name for key, path in staged_outputs.items()
        }
        final_first_frame = output_dir / staged_first_frame.name
        output_manifest: dict[str, Any] = {
            **{key: str(path) for key, path in final_paths.items()},
            "first_frame_path": str(final_first_frame),
            "master_sha256": sha256_file(staged_outputs["master_path"]),
            "web_sha256": sha256_file(staged_outputs["web_path"]),
            "poster_sha256": sha256_file(staged_outputs["poster_path"]),
            "first_frame_sha256": sha256_file(staged_first_frame),
        }
        if "webm_path" in staged_outputs:
            output_manifest["webm_sha256"] = sha256_file(
                staged_outputs["webm_path"]
            )
        manifest_path = output_dir / f"{stem}-manifest.json"
        output_manifest["manifest_path"] = str(manifest_path)
        manifest = {
            "job_id": job_id,
            "policy": {
                "developer_api_used": False,
                "api_key_auth_disabled": True,
                "chatgpt_backend": "web_only",
                "grok_transport": "grok_build_membership_cli",
            },
            "request": request,
            "first_frame": first_frame,
            "video": video,
            "probe": {
                "source": source_probe,
                "web": web_probe,
                "webm": webm_probe,
            },
            "outputs": output_manifest,
        }
        staged_manifest = staging_dir / manifest_path.name
        partial = staged_manifest.with_suffix(".json.part")
        partial.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(partial, staged_manifest)
        result_manifest = {
            **manifest,
            "outputs": {
                **output_manifest,
                "manifest_sha256": sha256_file(staged_manifest),
            },
        }

        store.update_if_status(
            job_id,
            {"running"},
            phase="publishing",
            submission="confirmed",
            retry_safe=False,
        )
        publish_files = {
            **{path: final_paths[key] for key, path in staged_outputs.items()},
            staged_first_frame: final_first_frame,
            staged_manifest: manifest_path,
        }
        _publish_bundle(publish_files)
        store.update_if_status(
            job_id,
            {"running"},
            status="completed",
            phase="completed",
            submission="confirmed",
            retry_safe=False,
            result_json=result_manifest,
            error=None,
        )
    except JobStateConflict as conflict:
        LOGGER.info(
            "job state changed concurrently; leaving terminal state intact: %s",
            conflict,
        )
    except ProviderFailure as error:
        LOGGER.exception("provider failure")
        try:
            store.update_if_status(
                job_id,
                {"queued", "running"},
                status=(
                    "submitted_unknown"
                    if error.submission == "unknown" and not error.retry_safe
                    else "failed"
                ),
                phase=error.phase,
                submission=error.submission,
                retry_safe=error.retry_safe,
                error=json.dumps(
                    {
                        "code": error.code,
                        "message": error.message,
                        "diagnostics": error.diagnostics or [],
                    },
                    ensure_ascii=False,
                ),
            )
        except JobStateConflict:
            pass
    except Exception as error:
        LOGGER.exception("job failed")
        current = store.get(job_id)
        try:
            store.update_if_status(
                job_id,
                {"queued", "running"},
                status=(
                    "submitted_unknown"
                    if current["submission"] == "unknown"
                    else "failed"
                ),
                phase="internal_error",
                submission=current["submission"],
                retry_safe=False,
                error=json.dumps(
                    {"code": "INTERNAL_ERROR", "message": str(error)},
                    ensure_ascii=False,
                ),
            )
        except JobStateConflict:
            pass
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if len(sys.argv) != 2:
        raise SystemExit("usage: grok-membership-media-worker JOB_ID")
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    execute_job(sys.argv[1])


if __name__ == "__main__":
    main()
