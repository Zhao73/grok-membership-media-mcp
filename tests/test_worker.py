from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from grok_membership_media_mcp.config import Settings
from grok_membership_media_mcp.providers import ProviderFailure
from grok_membership_media_mcp.worker import (
    _image_dimensions,
    _normalize_first_frame,
    _publish_bundle,
    _size_for_aspect_ratio,
    _validate_video,
)


def _settings(tmp_path: Path) -> Settings:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg is required")
    return Settings(
        state_dir=tmp_path / "state",
        allowed_roots=(tmp_path.resolve(),),
        grok_bin="/fake/grok",
        chatgpt_imagegen_bin="/fake/chatgpt-imagegen",
        ffmpeg_bin=ffmpeg,
        ffprobe_bin=ffprobe,
        grok_timeout_seconds=10,
        chatgpt_timeout_seconds=10,
    )


def test_requested_gpt_sizes_have_the_requested_ratio():
    for ratio in ("16:9", "9:16", "1:1", "4:3", "3:4"):
        width, height = map(int, _size_for_aspect_ratio(ratio).split("x"))
        expected_width, expected_height = map(int, ratio.split(":"))
        assert abs((width / height) - (expected_width / expected_height)) < 0.001


def test_first_frame_is_normalized_to_exact_aspect(tmp_path: Path):
    settings = _settings(tmp_path)
    source = tmp_path / "source.png"
    destination = tmp_path / "normalized.png"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=300x200",
            "-frames:v",
            "1",
            str(source),
        ],
        check=True,
    )
    result = _normalize_first_frame(
        settings,
        source=source,
        destination=destination,
        aspect_ratio="16:9",
    )
    assert result["dimensions"] == [1280, 720]
    assert _image_dimensions(settings, destination) == (1280, 720)


def test_video_validation_checks_duration_resolution_and_decode(tmp_path: Path):
    settings = _settings(tmp_path)
    video = tmp_path / "test.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1280x720:r=24:d=6",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    payload = _validate_video(
        settings,
        video,
        expected_duration=6,
        expected_resolution="720p",
        expected_aspect_ratio="16:9",
        require_h264=True,
        require_no_audio=True,
    )
    assert payload["format"]["duration"].startswith("6.")
    with pytest.raises(ProviderFailure):
        _validate_video(
            settings,
            video,
            expected_duration=10,
            expected_resolution="720p",
            expected_aspect_ratio="9:16",
        )


def test_360p_video_is_not_accepted_as_480p(tmp_path: Path):
    settings = _settings(tmp_path)
    video = tmp_path / "360p.mp4"
    subprocess.run(
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=640x360:r=24:d=6",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    with pytest.raises(ProviderFailure, match="below 480p"):
        _validate_video(
            settings,
            video,
            expected_duration=6,
            expected_resolution="480p",
            expected_aspect_ratio="16:9",
        )


def test_publish_bundle_rolls_back_partial_move(monkeypatch, tmp_path: Path):
    stage_one = tmp_path / "one.stage"
    stage_two = tmp_path / "two.stage"
    final_one = tmp_path / "one.bin"
    final_two = tmp_path / "two.bin"
    stage_one.write_bytes(b"one")
    stage_two.write_bytes(b"two")
    real_link = os.link

    def fail_second(source, destination, *args, **kwargs):
        if Path(source) == stage_two and Path(destination) == final_two:
            raise OSError("simulated publish failure")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr("grok_membership_media_mcp.worker.os.link", fail_second)
    with pytest.raises(OSError):
        _publish_bundle({stage_one: final_one, stage_two: final_two})
    assert stage_one.read_bytes() == b"one"
    assert stage_two.read_bytes() == b"two"
    assert not final_one.exists()
    assert not final_two.exists()


def test_publish_bundle_never_overwrites_racing_destination(tmp_path: Path):
    staged = tmp_path / "asset.stage"
    final = tmp_path / "asset.bin"
    staged.write_bytes(b"ours")
    final.write_bytes(b"theirs")
    with pytest.raises(FileExistsError):
        _publish_bundle({staged: final})
    assert final.read_bytes() == b"theirs"
    assert staged.read_bytes() == b"ours"
