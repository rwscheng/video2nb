from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image

from coursera_notes.config import VisualsConfig
from coursera_notes.models import Course, Lecture, Lesson, Module
from coursera_notes.output.paths import lecture_relative_dir
from coursera_notes.pipeline.extract import ExtractResult
from coursera_notes.video.probe import VideoInfo


def test_extract_course_reuses_current_visuals_but_reprocesses_changed_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    from coursera_notes.pipeline import extract as extract_pipeline

    lecture = Lecture(
        course_id="c1",
        module_id="m1",
        module_index=1,
        module_name="Introduction",
        module_slug="introduction",
        lesson_id="l1",
        lesson_name="Welcome",
        lesson_slug="welcome",
        lesson_index=1,
        lecture_id="i1",
        lecture_index=1,
        lecture_name="Welcome",
        lecture_slug="welcome",
    )
    course = Course(
        id="c1",
        name="Course",
        slug="course",
        modules=[
            Module(
                id="m1",
                name="Introduction",
                slug="introduction",
                index=1,
                lessons=[
                    Lesson(id="l1", name="Welcome", slug="welcome", index=1, lectures=[lecture])
                ],
            )
        ],
    )
    course_root = tmp_path / "course"
    course_root.mkdir()
    (course_root / "course.json").write_text(course.model_dump_json())
    lecture_dir = course_root / lecture_relative_dir(lecture)
    lecture_dir.mkdir(parents=True)
    video_path = lecture_dir / "video.mp4"
    video_path.write_bytes(b"video")
    transcript_path = lecture_dir / "transcript.txt"
    transcript_path.write_text("00:00:01 Look at this diagram.")

    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.probe_video",
        lambda _: VideoInfo(duration_seconds=8, width=320, height=180, frame_rate=30),
    )
    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.detect_scene_changes",
        lambda *args, **kwargs: [],
    )

    def fake_frame(_: Path, timestamp: float, destination: Path, **kwargs: object) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (320, 180), "white")
        image.putpixel((int(timestamp * 10), 50), (20, 40, 200))
        image.save(destination)
        return destination

    monkeypatch.setattr("coursera_notes.pipeline.extract.extract_frame", fake_frame)
    monkeypatch.setattr("coursera_notes.pipeline.extract.is_low_value_image", lambda _: False)
    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.deduplicate_images",
        lambda candidates, max_distance: candidates,
    )
    options = VisualsConfig()
    first = extract_pipeline.extract_course(course_root, visuals=options)
    assert first.processed_lectures == 1

    original_extract = extract_pipeline.extract_lecture
    calls: list[Path] = []

    def counted_extract(
        video: Path, transcript: Path | None, output: Path, **kwargs: object
    ) -> ExtractResult:
        calls.append(video)
        return original_extract(video, transcript, output, **kwargs)

    monkeypatch.setattr(extract_pipeline, "extract_lecture", counted_extract)
    extract_pipeline.extract_course(course_root, visuals=options)
    assert calls == []

    transcript_path.write_text("00:00:01 Notice this graph changed.")
    os.utime(transcript_path, None)
    extract_pipeline.extract_course(course_root, visuals=options)
    assert calls == [video_path]
    assert json.loads((lecture_dir / "visual_manifest.json").read_text())
