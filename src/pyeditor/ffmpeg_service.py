from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .models import ClipSegment, RenderJob

ProgressCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


class FfmpegNotFoundError(RuntimeError):
    pass


def ensure_ffmpeg_available() -> None:
    if _resolve_binary("ffmpeg") is None or _resolve_binary("ffprobe") is None:
        raise FfmpegNotFoundError(
            "ffmpeg and ffprobe were not found. Install them globally or keep the bundled tools folder in this workspace."
        )


def get_ffmpeg_binary() -> str:
    ensure_ffmpeg_available()
    return _get_binary("ffmpeg")


def get_ffprobe_binary() -> str:
    ensure_ffmpeg_available()
    return _get_binary("ffprobe")


def probe_duration(source_path: Path) -> float:
    ensure_ffmpeg_available()
    command = [
        _get_binary("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(source_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    duration_text = payload.get("format", {}).get("duration")
    if duration_text is None:
        raise RuntimeError(f"Could not read duration for {source_path}")
    return float(duration_text)


def render_job(
    job: RenderJob,
    progress_callback: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
) -> None:
    ensure_ffmpeg_available()
    if not job.clips:
        raise ValueError("Render job does not contain any clips.")

    job.output_path.parent.mkdir(parents=True, exist_ok=True)

    clip_durations = [max(clip.trim_duration or 0.0, 0.001) for clip in job.clips]
    total_trim_duration = sum(clip_durations)

    def emit_status(message: str) -> None:
        if status_callback is not None:
            status_callback(message)

    def emit_progress(value: float) -> None:
        if progress_callback is not None:
            progress_callback(max(0.0, min(value, 1.0)))

    with tempfile.TemporaryDirectory(prefix="pyeditor-") as temp_dir_text:
        temp_dir = Path(temp_dir_text)
        segment_files: list[Path] = []
        completed_trim_duration = 0.0

        emit_progress(0.0)

        for index, (clip, clip_duration) in enumerate(zip(job.clips, clip_durations, strict=False), start=1):
            segment_path = temp_dir / f"segment_{index:03d}.mp4"
            emit_status(f"Trimming {clip.display_name} -> {segment_path.name}")
            _render_segment(
                clip,
                segment_path,
                duration_seconds=clip_duration,
                progress_callback=lambda fraction, base=completed_trim_duration, span=clip_duration: emit_progress(
                    0.9 * ((base + (span * fraction)) / total_trim_duration)
                ),
            )
            segment_files.append(segment_path)
            completed_trim_duration += clip_duration
            emit_progress(0.9 * (completed_trim_duration / total_trim_duration))

        concat_file = temp_dir / "segments.txt"
        concat_file.write_text(
            "\n".join(f"file '{segment.as_posix()}'" for segment in segment_files),
            encoding="utf-8",
        )

        emit_status(f"Concatenating {len(segment_files)} segments into {job.output_path.name}")
        _concat_segments(
            concat_file,
            job.output_path,
            duration_seconds=total_trim_duration,
            progress_callback=lambda fraction: emit_progress(0.9 + (0.1 * fraction)),
        )
        emit_progress(1.0)
        emit_status(f"Finished {job.output_path}")


def _render_segment(
    clip: ClipSegment,
    output_path: Path,
    duration_seconds: float,
    progress_callback: ProgressCallback | None = None,
) -> None:
    start_seconds = max(0.0, clip.start_seconds)
    end_seconds = clip.effective_end

    command = [
        _get_binary("ffmpeg"),
        "-y",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-ss",
        _format_timestamp(start_seconds),
        "-i",
        str(clip.source_path),
    ]

    if end_seconds is not None and end_seconds > start_seconds:
        duration_seconds = end_seconds - start_seconds
        command.extend(["-t", _format_timestamp(duration_seconds)])

    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )

    _run_ffmpeg_with_progress(command, duration_seconds, progress_callback)


def _concat_segments(
    concat_file: Path,
    output_path: Path,
    duration_seconds: float,
    progress_callback: ProgressCallback | None = None,
) -> None:
    command = [
        _get_binary("ffmpeg"),
        "-y",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_path),
    ]
    _run_ffmpeg_with_progress(command, duration_seconds, progress_callback)


def _format_timestamp(value: float) -> str:
    total_milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _parse_progress_timestamp(value: str) -> float:
    hours_text, minutes_text, seconds_text = value.split(":")
    return (int(hours_text) * 3600) + (int(minutes_text) * 60) + float(seconds_text)


def _run_ffmpeg_with_progress(
    command: list[str],
    duration_seconds: float,
    progress_callback: ProgressCallback | None,
) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    latest_fraction = 0.0
    if progress_callback is not None:
        progress_callback(0.0)

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key == "out_time" and duration_seconds > 0 and progress_callback is not None:
            try:
                seconds = _parse_progress_timestamp(value)
            except ValueError:
                continue
            latest_fraction = max(latest_fraction, min(seconds / duration_seconds, 1.0))
            progress_callback(latest_fraction)
        elif key == "progress" and value == "end" and progress_callback is not None:
            progress_callback(1.0)

    stderr_text = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, stderr=stderr_text)

    if progress_callback is not None and latest_fraction < 1.0:
        progress_callback(1.0)


def _get_binary(name: str) -> str:
    binary_path = _resolve_binary(name)
    if binary_path is None:
        raise FfmpegNotFoundError(
            f"Required binary '{name}' was not found in PATH or the workspace tools folder."
        )
    return str(binary_path)


@lru_cache(maxsize=2)
def _resolve_binary(name: str) -> Path | None:
    path_name = f"{name}.exe" if shutil.which("where") is not None else name

    path_match = shutil.which(name)
    if path_match is not None:
        return Path(path_match)

    workspace_root = Path(__file__).resolve().parents[2]
    bundled_matches = sorted(workspace_root.glob(f"tools/**/{path_name}"))
    if bundled_matches:
        return bundled_matches[0]

    return None
