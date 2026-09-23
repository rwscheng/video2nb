from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from coursera_notes.coursera.models import LectureMetadata, SubtitleSource
from coursera_notes.models import Course, Lecture, Lesson, Module
from coursera_notes.pipeline.extract import ExtractResult, ExtractSummary
from coursera_notes.pipeline.fetch import FetchSummary


def _course() -> Course:
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
        duration_seconds=120,
    )
    return Course(
        id="c1",
        name="Machine Learning",
        slug="machine-learning",
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


class FakeCourseraClient:
    def __init__(self, cauth: str) -> None:
        self.cauth = cauth
        self.metadata_calls = 0

    def __enter__(self) -> FakeCourseraClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_course(self, slug: str) -> Course:
        assert slug == "machine-learning"
        return _course()

    def get_lecture_metadata(self, course_id: str, item_id: str) -> LectureMetadata:
        self.metadata_calls += 1
        return LectureMetadata(
            video_sources=[],
            subtitle_sources=[],
            transcript_sources=[
                SubtitleSource(language="en", url="https://cdn.example/caption.txt", format="txt")
            ],
        )


def test_cli_help_lists_required_commands() -> None:
    from coursera_notes.cli import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("inspect", "fetch", "extract", "build", "package", "local"):
        assert command in result.stdout


def test_local_command_uses_shared_extraction_and_does_not_copy_video(
    tmp_path: Path, monkeypatch
) -> None:
    from coursera_notes.cli import app

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"video")
    transcript = tmp_path / "lecture.srt"
    transcript.write_text("1\n00:00:01,000 --> 00:00:02,000\nLook at this chart.\n")
    output = tmp_path / "output"
    calls: list[tuple[Path, Path | None, Path]] = []

    def fake_extract(
        video_path: Path,
        transcript_path: Path | None,
        output_dir: Path,
        **kwargs: object,
    ) -> ExtractResult:
        calls.append((video_path, transcript_path, output_dir))
        return ExtractResult(candidate_count=1, visual_count=1)

    monkeypatch.setattr("coursera_notes.cli.extract_lecture", fake_extract)
    result = CliRunner().invoke(
        app,
        [
            "--quiet",
            "local",
            str(video),
            "--transcript",
            str(transcript),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(video, output / "lecture/subtitles.srt", output / "lecture")]
    assert not (output / "lecture/lecture.mp4").exists()


def test_inspect_reports_languages_without_downloads(monkeypatch) -> None:
    from coursera_notes import cli

    clients: list[FakeCourseraClient] = []

    def factory(cauth: str) -> FakeCourseraClient:
        client = FakeCourseraClient(cauth)
        clients.append(client)
        return client

    monkeypatch.setattr(cli, "CourseraClient", factory)
    result = CliRunner().invoke(
        cli.app,
        ["inspect", "machine-learning", "--cauth", "CAUTH=private-token"],
    )

    assert result.exit_code == 0, result.output
    assert "Available transcript languages: en" in result.output
    assert "private-token" not in result.output
    assert clients[0].metadata_calls == 1


def test_build_calls_fetch_extract_and_package_in_order(tmp_path: Path, monkeypatch) -> None:
    from coursera_notes import cli

    calls: list[str] = []
    course = _course()
    course_dir = tmp_path / "machine-learning"
    monkeypatch.setattr(cli, "CourseraClient", FakeCourseraClient)

    def fake_fetch(*args: object, **kwargs: object) -> FetchSummary:
        calls.append("fetch")
        return FetchSummary(
            course_dir=course_dir,
            total_lectures=1,
            complete_lectures=1,
            failed_lectures=0,
        )

    def fake_extract(*args: object, **kwargs: object) -> ExtractSummary:
        calls.append("extract")
        return ExtractSummary(
            course_name=course.name,
            total_lectures=1,
            processed_lectures=1,
            failed_lectures=0,
        )

    def fake_package(root: Path, selected_course: Course) -> Path:
        calls.append("package")
        assert selected_course == course
        return root / "notebooklm"

    monkeypatch.setattr(cli, "fetch_course", fake_fetch)
    monkeypatch.setattr(cli, "extract_course", fake_extract)
    monkeypatch.setattr(cli, "package_notebooklm", fake_package)
    result = CliRunner().invoke(
        cli.app,
        ["build", "machine-learning", "--cauth", "private-token", "--output", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["fetch", "extract", "package"]
    assert "private-token" not in result.output


def test_build_without_video_packages_transcripts_without_extraction(
    tmp_path: Path, monkeypatch
) -> None:
    from coursera_notes import cli

    calls: list[str] = []
    course_dir = tmp_path / "machine-learning"
    monkeypatch.setattr(cli, "CourseraClient", FakeCourseraClient)

    def fake_fetch(*args: object, **kwargs: object) -> FetchSummary:
        calls.append("fetch")
        return FetchSummary(
            course_dir=course_dir,
            total_lectures=1,
            complete_lectures=1,
            failed_lectures=0,
        )

    def forbidden_extract(*args: object, **kwargs: object) -> ExtractSummary:
        raise AssertionError("--no-video must skip visual extraction")

    def fake_package(root: Path, selected_course: Course) -> Path:
        calls.append("package")
        return root / "notebooklm"

    monkeypatch.setattr(cli, "fetch_course", fake_fetch)
    monkeypatch.setattr(cli, "extract_course", forbidden_extract)
    monkeypatch.setattr(cli, "package_notebooklm", fake_package)
    result = CliRunner().invoke(
        cli.app,
        ["build", "machine-learning", "--cauth", "private-token", "--no-video"],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["fetch", "package"]
    assert "Visual extraction skipped" in result.output
