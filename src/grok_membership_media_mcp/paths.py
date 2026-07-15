from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path


class PathPolicyError(ValueError):
    pass


def resolve_allowed(
    raw_path: str | Path,
    allowed_roots: tuple[Path, ...],
    *,
    must_exist: bool = False,
) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise PathPolicyError("path must be absolute")
    resolved = path.resolve(strict=must_exist)
    if not any(resolved == root or resolved.is_relative_to(root) for root in allowed_roots):
        raise PathPolicyError(f"path is outside allowed roots: {resolved}")
    return resolved


def safe_stem(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return (normalized or "grok-video")[:80]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sniff_image_mime(path: Path) -> str | None:
    header = path.read_bytes()[:16]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def atomic_copy(source: Path, destination: Path, *, overwrite: bool = False) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"destination already exists: {destination}")
    partial = destination.with_name(f".{destination.name}.{os.getpid()}.part")
    try:
        with source.open("rb") as src, partial.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination
