"""Extract one scaled JPEG at a time with ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path


def extract_frame(
    video_path: Path,
    timestamp_seconds: float,
    destination: Path,
    jpeg_quality: int = 88,
    timeout_seconds: float = 45,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    quality = max(2, min(31, round(31 - (jpeg_quality / 100) * 29)))
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-threads",
        "1",
        "-ss",
        f"{max(timestamp_seconds, 0):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=w='min(1920,iw)':h=-2",
        "-q:v",
        str(quality),
        "-y",
        str(destination),
    ]
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=timeout_seconds
    )
    if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg could not extract a frame at the requested timestamp")
    return destination
