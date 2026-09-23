import json
from pathlib import Path

from coursera_notes.coursera.models import LectureMetadata, SubtitleSource, VideoSource
from coursera_notes.models import Course, Lecture, Lesson, Module
from coursera_notes.pipeline.fetch import _atomic_json, fetch_course


def _course() -> Course:
    lectures = [
        Lecture(
            course_id="c1",
            module_id="m1",
            module_index=1,
            module_name="Week",
            module_slug="week",
            lesson_id="l1",
            lesson_name="Lesson",
            lesson_slug="lesson",
            lesson_index=1,
            lecture_id=lecture_id,
            lecture_index=index,
            lecture_name=lecture_id,
            lecture_slug=lecture_id,
        )
        for index, lecture_id in enumerate(("ok", "broken"), start=1)
    ]
    return Course(
        id="c1",
        name="Course",
        slug="course",
        modules=[
            Module(
                id="m1",
                name="Week",
                slug="week",
                index=1,
                lessons=[Lesson(id="l1", name="Lesson", slug="lesson", index=1, lectures=lectures)],
            )
        ],
    )


class FakeClient:
    def get_lecture_metadata(self, course_id: str, item_id: str) -> LectureMetadata:
        if item_id == "broken":
            raise RuntimeError("response contains a signed URL that must not be stored")
        assert course_id == "c1"
        return LectureMetadata(
            video_sources=[VideoSource(resolution="720p", url="https://cdn.example/private-video")],
            subtitle_sources=[],
            transcript_sources=[
                SubtitleSource(
                    language="en", url="https://cdn.example/private-transcript", format="txt"
                )
            ],
        )


def test_fetch_uses_one_metadata_result_and_continues_after_lecture_failure(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, str]] = []

    def write_download(url: str, destination: Path, **kwargs: object) -> object:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"saved from {url}")
        calls.append((url, destination.name))
        return type("Result", (), {"skipped": False, "bytes_written": destination.stat().st_size})()

    monkeypatch.setattr("coursera_notes.pipeline.fetch.download_file", write_download)

    summary = fetch_course(
        FakeClient(),
        _course(),
        tmp_path,
        workers=1,
        metadata_delay_seconds=0,
    )

    assert summary.total_lectures == 2
    assert summary.complete_lectures == 1
    assert summary.failed_lectures == 1
    assert len(calls) == 2
    course_dir = tmp_path / "course"
    assert (course_dir / "01_week/01_lesson/01_ok/video.mp4").is_file()
    lecture_json = (course_dir / "01_week/01_lesson/01_ok/lecture.json").read_text()
    assert "private-video" not in lecture_json and "private-transcript" not in lecture_json
    all_metadata = "\n".join(path.read_text() for path in course_dir.rglob("*.json"))
    assert "private" not in all_metadata and "signed URL" not in all_metadata


def test_atomic_json_ignores_preexisting_predictable_temp_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "protected.json"
    outside.write_text("keep me")
    target = tmp_path / "course.json"
    temporary_symlink = tmp_path / "course.json.tmp"
    temporary_symlink.symlink_to(outside)

    _atomic_json(target, {"name": "Course"})

    assert json.loads(target.read_text()) == {"name": "Course"}
    assert outside.read_text() == "keep me"
    assert temporary_symlink.is_symlink()


def test_fetch_replaces_assets_when_resolution_or_language_changes(
    tmp_path: Path, monkeypatch
) -> None:
    course = _course()
    lecture = course.lectures[0]
    single = Course(
        id=course.id,
        name=course.name,
        slug=course.slug,
        modules=[
            Module(
                id=lecture.module_id,
                name=lecture.module_name,
                slug=lecture.module_slug,
                index=lecture.module_index,
                lessons=[
                    Lesson(
                        id=lecture.lesson_id,
                        name=lecture.lesson_name or "Lesson",
                        slug=lecture.lesson_slug or "lesson",
                        index=lecture.lesson_index,
                        lectures=[lecture],
                    )
                ],
            )
        ],
    )
    calls: list[tuple[str, bool]] = []

    def write_download(
        url: str, destination: Path, *, overwrite: bool = False, **kwargs: object
    ) -> object:
        if destination.is_file() and not overwrite:
            return type("Result", (), {"skipped": True, "bytes_written": 0})()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(url)
        calls.append((url, overwrite))
        return type(
            "Result",
            (),
            {"skipped": False, "bytes_written": destination.stat().st_size},
        )()

    monkeypatch.setattr("coursera_notes.pipeline.fetch.download_file", write_download)

    first_metadata = LectureMetadata(
        video_sources=[VideoSource(resolution="720p", url="https://cdn.example/720.mp4")],
        subtitle_sources=[],
        transcript_sources=[
            SubtitleSource(language="en", url="https://cdn.example/en.txt", format="txt")
        ],
    )
    second_metadata = LectureMetadata(
        video_sources=[VideoSource(resolution="1080p", url="https://cdn.example/1080.mp4")],
        subtitle_sources=[],
        transcript_sources=[
            SubtitleSource(language="fr", url="https://cdn.example/fr.txt", format="txt")
        ],
    )

    class VersionClient:
        def __init__(self, metadata: LectureMetadata) -> None:
            self.metadata = metadata

        def get_lecture_metadata(self, course_id: str, item_id: str) -> LectureMetadata:
            return self.metadata

    fetch_course(
        VersionClient(first_metadata),
        single,
        tmp_path,
        resolution="720p",
        language="en",
        workers=1,
        metadata_delay_seconds=0,
    )
    second_summary = fetch_course(
        VersionClient(second_metadata),
        single,
        tmp_path,
        resolution="1080p",
        language="fr",
        workers=1,
        metadata_delay_seconds=0,
    )

    output_dir = second_summary.course_dir / "01_week/01_lesson/01_ok"
    record = json.loads((output_dir / "lecture.json").read_text())
    assert (output_dir / "video.mp4").read_text() == "https://cdn.example/1080.mp4"
    assert (output_dir / "transcript.txt").read_text() == "https://cdn.example/fr.txt"
    assert record["selected_video_resolution"] == "1080p"
    assert record["selected_transcript_language"] == "fr"
    assert calls == [
        ("https://cdn.example/720.mp4", False),
        ("https://cdn.example/en.txt", True),
        ("https://cdn.example/1080.mp4", True),
        ("https://cdn.example/fr.txt", True),
    ]
