from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from .ffmpeg_service import get_ffmpeg_binary


PROXY_PROFILE_VERSION = "v3"
PROXY_SCALE_FILTER = "scale=360:-2,fps=5"
PROXY_CRF = "38"


def get_proxy_cache_dir() -> Path:
    workspace_root = Path(__file__).resolve().parents[2]
    cache_dir = workspace_root / ".pyeditor-cache" / "proxies"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cached_proxy_path(source_path: Path) -> Path:
    stat = source_path.stat()
    cache_key = hashlib.sha256(
        f"{PROXY_PROFILE_VERSION}|{source_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:16]
    sanitized_stem = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in source_path.stem)
    file_name = f"{sanitized_stem}-{cache_key}.mp4"
    return get_proxy_cache_dir() / file_name


def find_existing_proxy(source_path: Path) -> Path | None:
    proxy_path = get_cached_proxy_path(source_path)
    if proxy_path.exists():
        return proxy_path
    return None


def ensure_proxy(source_path: Path) -> Path:
    existing_proxy = find_existing_proxy(source_path)
    if existing_proxy is not None:
        return existing_proxy

    proxy_path = get_cached_proxy_path(source_path)
    temp_proxy_path = proxy_path.with_suffix(".tmp.mp4")
    if temp_proxy_path.exists():
        temp_proxy_path.unlink()

    command = [
        get_ffmpeg_binary(),
        "-y",
        "-i",
        str(source_path),
        "-an",
        "-vf",
        PROXY_SCALE_FILTER,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        PROXY_CRF,
        "-movflags",
        "+faststart",
        str(temp_proxy_path),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        os.replace(temp_proxy_path, proxy_path)
    except Exception:
        if temp_proxy_path.exists():
            temp_proxy_path.unlink()
        raise

    return proxy_path