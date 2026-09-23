"""Small ffprobe wrapper; never loads video data into Python memory."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class VideoInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration_seconds: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    frame_rate: float | None = Field(default=None, gt=0)


def probe_video(path: Path, timeout_seconds: float = 30) -> VideoInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,duration:format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=timeout_seconds
    )
    if result.returncode:
        raise RuntimeError("ffprobe could not read the video")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid metadata") from exc
    streams = data.get("streams", [])
    stream = streams[0] if streams else {}
    duration = stream.get("duration") or data.get("format", {}).get("duration")
    frame_rate = _fraction(stream.get("r_frame_rate"))
    return VideoInfo(
        duration_seconds=_positive_float(duration),
        width=stream.get("width"),
        height=stream.get("height"),
        frame_rate=frame_rate,
    )


def _positive_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _fraction(value: object) -> float | None:
    if not isinstance(value, str):
        return _positive_float(value)
    try:
        numerator, denominator = value.split("/", 1)
        result = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return _positive_float(value)
    return result if result > 0 else None
