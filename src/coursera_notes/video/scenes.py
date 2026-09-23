"""PySceneDetect adapter for slide and scene boundary timestamps."""

from __future__ import annotations

from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video


def detect_scene_changes(
    video_path: Path,
    threshold: float = 30.0,
    minimum_scene_seconds: float = 1.5,
) -> list[float]:
    video = open_video(str(video_path), backend="opencv")
    manager = SceneManager()
    minimum_frames = max(1, round(video.frame_rate * minimum_scene_seconds))
    try:
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=minimum_frames))
        manager.detect_scenes(video=video)
        return [
            start.get_seconds()
            for start, _ in manager.get_scene_list()
            if start.get_seconds() > 0.25
        ]
    finally:
        capture = getattr(video, "capture", None)
        release = getattr(capture, "release", None)
        if callable(release):
            release()
