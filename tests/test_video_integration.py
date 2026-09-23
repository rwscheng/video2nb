import shutil
import subprocess
from pathlib import Path

import pytest

from coursera_notes.video.probe import probe_video
from coursera_notes.video.scenes import detect_scene_changes
from coursera_notes.video.screenshots import extract_frame

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="FFmpeg and ffprobe are required",
    ),
]


def _make_test_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=10:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
            "-map",
            "[v]",
            "-c:v",
            "mpeg4",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_synthetic_video_probe_scene_detection_and_frame_extraction(tmp_path: Path) -> None:
    video = tmp_path / "two slides; safe.mp4"
    _make_test_video(video)

    info = probe_video(video)
    scenes = detect_scene_changes(video, threshold=10, minimum_scene_seconds=0.3)
    screenshot = extract_frame(video, 1.2, tmp_path / "visuals" / "shot.jpg")

    assert info.duration_seconds == pytest.approx(2, abs=0.2)
    assert info.width == 320 and info.height == 180
    assert any(0.7 <= scene <= 1.3 for scene in scenes)
    assert screenshot.is_file() and screenshot.stat().st_size > 0
