"""Course-level lecture metadata and asset fetching with per-lecture isolation."""

from __future__ import annotations

import json
import random
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from coursera_notes.coursera.downloader import DownloadError, download_file
from coursera_notes.coursera.models import FetchFailure, LectureMetadata
from coursera_notes.coursera.parser import select_transcript_source, select_video_source
from coursera_notes.logging import get_logger
from coursera_notes.models import Course, Lecture
from coursera_notes.output.paths import course_output_dir, lecture_relative_dir, safe_join


class MetadataClient(Protocol):
    def get_lecture_metadata(self, course_id: str, item_id: str) -> LectureMetadata: ...


class FetchSummary(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    course_dir: Path
    total_lectures: int
    complete_lectures: int
    failed_lectures: int
    failures: list[FetchFailure] = Field(default_factory=list)


class _LectureResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    lecture_id: str
    complete: bool
    failures: list[FetchFailure] = Field(default_factory=list)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _lecture_dir(course_dir: Path, lecture: Lecture) -> Path:
    return safe_join(course_dir, *lecture_relative_dir(lecture).parts)


def _metadata_record(
    lecture: Lecture,
    *,
    video_resolution: str | None,
    video_ok: bool,
    transcript_languages: list[str],
    selected_transcript_language: str | None,
    selected_transcript_format: str | None,
    transcript_ok: bool,
    subtitle_ok: bool,
    metadata_ok: bool,
) -> dict[str, object]:
    return {
        **lecture.model_dump(mode="json"),
        "metadata_available": metadata_ok,
        "selected_video_resolution": video_resolution,
        "available_subtitle_languages": transcript_languages,
        "selected_transcript_language": selected_transcript_language,
        "selected_transcript_format": selected_transcript_format,
        "video_downloaded": video_ok,
        "transcript_downloaded": transcript_ok,
        "subtitle_downloaded": subtitle_ok,
    }


def _save_lecture_metadata(path: Path, record: dict[str, object]) -> None:
    _atomic_json(path / "lecture.json", record)


def _previous_lecture_metadata(path: Path) -> dict[str, object]:
    try:
        metadata_path = safe_join(path, "lecture.json")
        if metadata_path.is_file():
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _remove_lecture_asset(directory: Path, filename: str) -> None:
    asset = safe_join(directory, filename)
    asset.unlink(missing_ok=True)


def _fetch_one(
    client: MetadataClient,
    lecture: Lecture,
    course_dir: Path,
    *,
    resolution: str,
    language: str,
    video: bool,
    transcript: bool,
    overwrite: bool,
    metadata_delay_seconds: float,
    metadata_jitter_seconds: float,
) -> _LectureResult:
    destination_dir = _lecture_dir(course_dir, lecture)
    destination_dir.mkdir(parents=True, exist_ok=True)
    previous_record = _previous_lecture_metadata(destination_dir)
    failures: list[FetchFailure] = []
    if metadata_delay_seconds + metadata_jitter_seconds > 0:
        time.sleep(metadata_delay_seconds + random.uniform(0, metadata_jitter_seconds))
    try:
        metadata = client.get_lecture_metadata(lecture.course_id, lecture.lecture_id)
    except Exception:
        failures.append(
            FetchFailure(
                lecture_id=lecture.lecture_id,
                lecture_name=lecture.lecture_name,
                kind="metadata_failed",
                message="Lecture metadata request failed",
            )
        )
        _save_lecture_metadata(
            destination_dir,
            _metadata_record(
                lecture,
                video_resolution=None,
                video_ok=not video,
                transcript_languages=[],
                selected_transcript_language=None,
                selected_transcript_format=None,
                transcript_ok=not transcript,
                subtitle_ok=not transcript,
                metadata_ok=False,
            ),
        )
        return _LectureResult(lecture_id=lecture.lecture_id, complete=False, failures=failures)

    selected_video = select_video_source(metadata.video_sources, resolution)
    txt_source = (
        select_transcript_source(metadata.transcript_sources, language) if transcript else None
    )
    subtitle_source = (
        select_transcript_source(metadata.subtitle_sources, language) if transcript else None
    )
    usable_subtitle = (
        subtitle_source if subtitle_source and subtitle_source.format in {"srt", "vtt"} else None
    )
    selected_transcript = txt_source or usable_subtitle
    selected_transcript_language = (
        selected_transcript.language if selected_transcript is not None else None
    )
    selected_transcript_format = (
        selected_transcript.format if selected_transcript is not None else None
    )
    video_ok = not video
    logger = get_logger("fetch")
    if video:
        if selected_video is None:
            failures.append(
                FetchFailure(
                    lecture_id=lecture.lecture_id,
                    lecture_name=lecture.lecture_name,
                    kind="video_missing",
                    message=f"No downloadable video source is available near {resolution}",
                )
            )
        else:
            logger.info(
                "✓ %s video — %s selected for requested %s",
                lecture.lecture_name,
                selected_video.resolution,
                resolution,
            )
            try:
                video_target = safe_join(destination_dir, "video.mp4")
                replace_video = overwrite or (
                    video_target.is_file()
                    and previous_record.get("selected_video_resolution")
                    != selected_video.resolution
                )
                download_file(
                    selected_video.url,
                    video_target,
                    overwrite=replace_video,
                )
                video_ok = True
            except DownloadError:
                failures.append(
                    FetchFailure(
                        lecture_id=lecture.lecture_id,
                        lecture_name=lecture.lecture_name,
                        kind="video_failed",
                        message="Video download failed",
                    )
                )

    txt_ok = False
    subtitle_ok = False
    if transcript:
        active_transcript_files: set[str] = set()
        if txt_source:
            active_transcript_files.add("transcript.txt")
        if usable_subtitle:
            active_transcript_files.add(f"subtitles.{usable_subtitle.format}")
        transcript_selection_changed = (
            previous_record.get("selected_transcript_language") != selected_transcript_language
            or previous_record.get("selected_transcript_format") != selected_transcript_format
        )
        transcript_files = {"transcript.txt", "subtitles.srt", "subtitles.vtt"}
        if transcript_selection_changed:
            for filename in transcript_files:
                _remove_lecture_asset(destination_dir, filename)
        else:
            for filename in transcript_files - active_transcript_files:
                _remove_lecture_asset(destination_dir, filename)

        if txt_source:
            try:
                transcript_target = safe_join(destination_dir, "transcript.txt")
                download_file(
                    txt_source.url,
                    transcript_target,
                    overwrite=overwrite or transcript_selection_changed,
                )
                txt_ok = True
            except DownloadError:
                failures.append(
                    FetchFailure(
                        lecture_id=lecture.lecture_id,
                        lecture_name=lecture.lecture_name,
                        kind="transcript_failed",
                        message="Official transcript download failed",
                    )
                )
        if usable_subtitle:
            target = safe_join(destination_dir, f"subtitles.{usable_subtitle.format}")
            try:
                download_file(
                    usable_subtitle.url,
                    target,
                    overwrite=overwrite or transcript_selection_changed,
                )
                subtitle_ok = True
            except DownloadError:
                failures.append(
                    FetchFailure(
                        lecture_id=lecture.lecture_id,
                        lecture_name=lecture.lecture_name,
                        kind="subtitle_failed",
                        message="Official subtitle download failed",
                    )
                )
        if not txt_source and not usable_subtitle:
            failures.append(
                FetchFailure(
                    lecture_id=lecture.lecture_id,
                    lecture_name=lecture.lecture_name,
                    kind="transcript_missing",
                    message=f"No official transcript or subtitle is available in {language}",
                )
            )
    transcript_ok = not transcript or txt_ok or subtitle_ok
    if transcript and (txt_ok or subtitle_ok) and failures:
        failures = [
            failure
            for failure in failures
            if failure.kind not in {"transcript_failed", "subtitle_failed"}
            or (failure.kind == "transcript_failed" and not subtitle_ok)
            or (failure.kind == "subtitle_failed" and not txt_ok)
        ]

    _save_lecture_metadata(
        destination_dir,
        _metadata_record(
            lecture,
            video_resolution=selected_video.resolution if selected_video else None,
            video_ok=video_ok,
            transcript_languages=metadata.subtitle_languages,
            selected_transcript_language=selected_transcript_language,
            selected_transcript_format=selected_transcript_format,
            transcript_ok=txt_ok or subtitle_ok,
            subtitle_ok=subtitle_ok,
            metadata_ok=True,
        ),
    )
    complete = video_ok and transcript_ok
    return _LectureResult(lecture_id=lecture.lecture_id, complete=complete, failures=failures)


def fetch_course(
    client: MetadataClient,
    course: Course,
    output_root: Path,
    *,
    resolution: str = "720p",
    language: str = "en",
    workers: int = 2,
    video: bool = True,
    transcript: bool = True,
    overwrite: bool = False,
    metadata_delay_seconds: float = 0.2,
    metadata_jitter_seconds: float = 0.3,
) -> FetchSummary:
    """Fetch all eligible lecture assets and record incomplete items without aborting."""
    if workers < 1:
        raise ValueError("workers must be at least one")
    course_dir = course_output_dir(output_root, course)
    course_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(course_dir / "course.json", course.model_dump(mode="json"))
    lectures = course.lectures
    logger = get_logger("fetch")
    logger.info("Course: %s", course.name)

    results: dict[str, _LectureResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_lecture = {
            executor.submit(
                _fetch_one,
                client,
                lecture,
                course_dir,
                resolution=resolution,
                language=language,
                video=video,
                transcript=transcript,
                overwrite=overwrite,
                metadata_delay_seconds=metadata_delay_seconds,
                metadata_jitter_seconds=metadata_jitter_seconds,
            ): lecture
            for lecture in lectures
        }
        for future in as_completed(future_to_lecture):
            lecture = future_to_lecture[future]
            try:
                result = future.result()
            except Exception:
                result = _LectureResult(
                    lecture_id=lecture.lecture_id,
                    complete=False,
                    failures=[
                        FetchFailure(
                            lecture_id=lecture.lecture_id,
                            lecture_name=lecture.lecture_name,
                            kind="lecture_failed",
                            message="Lecture processing failed",
                        )
                    ],
                )
            results[lecture.lecture_id] = result
            logger.info("[%d/%d] %s", len(results), len(lectures), lecture.lecture_name)

    ordered_results = [results[lecture.lecture_id] for lecture in lectures]
    failures = [failure for result in ordered_results for failure in result.failures]
    complete_count = sum(result.complete for result in ordered_results)
    summary = FetchSummary(
        course_dir=course_dir,
        total_lectures=len(lectures),
        complete_lectures=complete_count,
        failed_lectures=len(lectures) - complete_count,
        failures=failures,
    )
    _atomic_json(course_dir / "fetch_report.json", summary.model_dump(mode="json"))
    return summary
