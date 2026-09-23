"""Transcript-guided and scene-guided screenshot extraction."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

from pydantic import BaseModel, Field

from coursera_notes.config import VisualsConfig
from coursera_notes.logging import get_logger
from coursera_notes.models import Course
from coursera_notes.output.paths import lecture_relative_dir, safe_join
from coursera_notes.transcript.parser import parse_timestamped_transcript
from coursera_notes.transcript.visual_cues import match_visual_cues
from coursera_notes.video.dedupe import deduplicate_images, is_low_value_image
from coursera_notes.video.models import ScreenshotCandidate, VisualRecord
from coursera_notes.video.probe import probe_video
from coursera_notes.video.scenes import detect_scene_changes
from coursera_notes.video.screenshots import extract_frame


class ExtractResult(BaseModel):
    candidate_count: int
    visual_count: int
    records: list[VisualRecord] = Field(default_factory=list)


class ExtractFailure(BaseModel):
    lecture_id: str
    lecture_name: str
    message: str


class ExtractSummary(BaseModel):
    course_name: str
    total_lectures: int
    processed_lectures: int
    failed_lectures: int
    failures: list[ExtractFailure] = Field(default_factory=list)


class _CandidateData(TypedDict):
    sources: set[str]
    transcript_context: str | None
    cue: str | None


def _input_state(path: Path | None) -> dict[str, int | str] | None:
    if path is None or not path.is_file():
        return None
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _processing_signature(
    video_path: Path,
    transcript_path: Path | None,
    language: str,
    visuals: VisualsConfig,
) -> str:
    payload = {
        "schema": 1,
        "video": _input_state(video_path),
        "transcript": _input_state(transcript_path),
        "language": language,
        "visuals": visuals.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _visuals_are_current(output_dir: Path, signature: str) -> bool:
    try:
        marker_path = safe_join(output_dir, ".visual_processing.json")
        manifest_path = safe_join(output_dir, "visual_manifest.json")
        notes_path = safe_join(output_dir, "visual_notes.md")
    except ValueError:
        return False
    if not marker_path.is_file() or not manifest_path.is_file() or not notes_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if (
        not isinstance(marker, dict)
        or marker.get("signature") != signature
        or not isinstance(records, list)
    ):
        return False
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("filename"), str):
            return False
        try:
            image_path = safe_join(output_dir, "visuals", record["filename"])
        except ValueError:
            return False
        if not image_path.is_file():
            return False
    return True


def _clip_timestamp(value: float, duration: float | None) -> float:
    value = max(0.0, value)
    if duration is not None and duration > 0:
        value = min(value, max(0.0, duration - 0.05))
    return round(value, 3)


def _timestamp(seconds: float) -> str:
    rounded = max(0, round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds_value = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds_value:02}"


def _filename(seconds: float, collision: int = 1) -> str:
    base = _timestamp(seconds).replace(":", "-")
    suffix = "" if collision == 1 else f"-{collision:02}"
    return f"{base}{suffix}.jpg"


def _limit_candidate_data(
    candidates: dict[float, _CandidateData],
    duration_seconds: float | None,
    max_visuals_per_hour: int,
) -> dict[float, _CandidateData]:
    """Bound FFmpeg work to three candidate frames per allowed final visual."""
    if duration_seconds is None or duration_seconds <= 0 or not candidates:
        return candidates
    hours = max(1, math.ceil(duration_seconds / 3600))
    bucket_count = max(1, max_visuals_per_hour * hours * 3)
    bucket_seconds = duration_seconds / bucket_count
    selected: dict[int, tuple[float, _CandidateData]] = {}
    for timestamp, data in candidates.items():
        bucket = min(bucket_count - 1, max(0, int(timestamp / bucket_seconds)))
        center = (bucket + 0.5) * bucket_seconds
        rank = (
            int("transcript_cue" in data["sources"]),
            int("scene_change" in data["sources"]),
            -abs(timestamp - center),
            -timestamp,
        )
        previous = selected.get(bucket)
        if previous is None:
            selected[bucket] = (timestamp, data)
            continue
        previous_timestamp, previous_data = previous
        previous_rank = (
            int("transcript_cue" in previous_data["sources"]),
            int("scene_change" in previous_data["sources"]),
            -abs(previous_timestamp - center),
            -previous_timestamp,
        )
        if rank > previous_rank:
            selected[bucket] = (timestamp, data)
    return {timestamp: data for timestamp, data in selected.values()}


def _write_notes(records: list[VisualRecord], path: Path) -> None:
    lines = ["# Lecture Visual Context", ""]
    if not records:
        lines.extend(["No useful visual moments were selected.", ""])
    for record in records:
        lines.extend(
            [
                f"## {record.timestamp}",
                "",
                f"![{record.timestamp}](visuals/{record.filename})",
                "",
            ]
        )
        if record.transcript_context:
            lines.extend(["Transcript context:", "", f"> {record.transcript_context}", ""])
        lines.append("Reason selected:")
        for source in record.sources:
            if source == "transcript_cue":
                cue = record.cue or "visual reference"
                lines.append(f'* Transcript visual cue: "{cue}"')
            elif source == "scene_change":
                lines.append("* Scene or slide change")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def extract_lecture(
    video_path: Path,
    transcript_path: Path | None,
    output_dir: Path,
    *,
    language: str = "en",
    visuals: VisualsConfig | None = None,
    extra_cue_patterns: Mapping[str, list[str]] | None = None,
) -> ExtractResult:
    """Extract and deduplicate useful moments from one local video."""
    options = visuals or VisualsConfig()
    logger = get_logger("extract")
    output_dir.mkdir(parents=True, exist_ok=True)
    visual_dir = safe_join(output_dir, "visuals")
    visual_dir.mkdir(parents=True, exist_ok=True)
    info = probe_video(video_path)
    transcript_text = ""
    if transcript_path is not None and transcript_path.is_file():
        transcript_text = transcript_path.read_text(encoding="utf-8-sig", errors="replace")
    segments = parse_timestamped_transcript(transcript_text)
    cues = match_visual_cues(segments, language=language, extra_patterns=extra_cue_patterns)

    candidate_data: dict[float, _CandidateData] = {}

    def add_candidate(
        timestamp_seconds: float,
        source: str,
        context: str | None = None,
        cue: str | None = None,
    ) -> None:
        timestamp = _clip_timestamp(timestamp_seconds, info.duration_seconds)
        data = candidate_data.setdefault(
            timestamp,
            {"sources": set(), "transcript_context": None, "cue": None},
        )
        data["sources"].add(source)
        if context and not data["transcript_context"]:
            data["transcript_context"] = context
        if cue and not data["cue"]:
            data["cue"] = cue

    for visual_cue in cues:
        for offset in (-options.cue_window_seconds, 0.0, options.cue_window_seconds):
            add_candidate(
                visual_cue.timestamp_seconds + offset,
                "transcript_cue",
                context=visual_cue.context,
                cue=visual_cue.cue,
            )

    scene_detection_succeeded = True
    try:
        scene_times = detect_scene_changes(
            video_path,
            threshold=options.scene_threshold,
            minimum_scene_seconds=options.minimum_scene_seconds,
        )
    except Exception:
        scene_detection_succeeded = False
        scene_times = []
        logger.warning(
            "Scene detection failed for %s; transcript cues will still be used", video_path.name
        )
    if scene_detection_succeeded and info.duration_seconds and info.duration_seconds > 0:
        add_candidate(min(0.5, info.duration_seconds / 2), "scene_change")
    for scene_time in scene_times:
        add_candidate(scene_time, "scene_change")

    candidate_data = _limit_candidate_data(
        candidate_data, info.duration_seconds, options.max_visuals_per_hour
    )
    candidate_count = len(candidate_data)
    candidates: list[ScreenshotCandidate] = []
    with tempfile.TemporaryDirectory(prefix="coursera-notes-", dir=output_dir) as temporary:
        temporary_dir = Path(temporary)
        for index, (timestamp_seconds, values) in enumerate(
            sorted(candidate_data.items()), start=1
        ):
            frame_path = temporary_dir / f"candidate-{index:04}.jpg"
            try:
                extract_frame(
                    video_path,
                    timestamp_seconds,
                    frame_path,
                    jpeg_quality=options.jpeg_quality,
                )
                if is_low_value_image(frame_path):
                    continue
            except Exception:
                continue
            candidates.append(
                ScreenshotCandidate(
                    timestamp_seconds=timestamp_seconds,
                    path=frame_path,
                    sources=values["sources"],
                    transcript_context=values["transcript_context"],
                    cue=values["cue"],
                )
            )

        kept = deduplicate_images(candidates, max_distance=options.hash_distance)
        if info.duration_seconds is not None:
            hours = max(1, math.ceil(info.duration_seconds / 3600))
            cap = options.max_visuals_per_hour * hours
            if len(kept) > cap:
                kept = sorted(
                    kept,
                    key=lambda item: (
                        int("transcript_cue" in item.sources),
                        int("scene_change" in item.sources),
                        item.quality_score,
                        -item.timestamp_seconds,
                    ),
                    reverse=True,
                )[:cap]
        kept.sort(key=lambda item: item.timestamp_seconds)

        filename_counts: dict[str, int] = {}
        records: list[VisualRecord] = []
        for candidate in kept:
            base = _filename(candidate.timestamp_seconds).removesuffix(".jpg")
            filename_counts[base] = filename_counts.get(base, 0) + 1
            filename = _filename(candidate.timestamp_seconds, filename_counts[base])
            shutil.copyfile(candidate.path, safe_join(visual_dir, filename))
            records.append(
                VisualRecord(
                    timestamp_seconds=candidate.timestamp_seconds,
                    timestamp=_timestamp(candidate.timestamp_seconds),
                    filename=filename,
                    sources=sorted(candidate.sources),
                    transcript_context=candidate.transcript_context,
                    cue=candidate.cue,
                )
            )

    safe_join(output_dir, "visual_manifest.json").write_text(
        json.dumps(
            [record.model_dump(mode="json") for record in records], ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    _write_notes(records, safe_join(output_dir, "visual_notes.md"))
    signature = _processing_signature(video_path, transcript_path, language, options)
    safe_join(output_dir, ".visual_processing.json").write_text(
        json.dumps({"signature": signature}, indent=2) + "\n", encoding="utf-8"
    )
    logger.info("✓ %d visual candidates; %d final screenshots", candidate_count, len(records))
    return ExtractResult(
        candidate_count=candidate_count, visual_count=len(records), records=records
    )


def _transcript_for(lecture_dir: Path) -> Path | None:
    for name in ("transcript.txt", "subtitles.srt", "subtitles.vtt"):
        try:
            path = safe_join(lecture_dir, name)
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def extract_course(
    course_root: Path,
    *,
    visuals: VisualsConfig | None = None,
    language: str = "en",
    force: bool = False,
) -> ExtractSummary:
    """Process existing videos independently and save a complete extraction report."""
    course_payload = json.loads(safe_join(course_root, "course.json").read_text(encoding="utf-8"))
    course = Course.model_validate(course_payload)
    logger = get_logger("extract")
    failures: list[ExtractFailure] = []
    processed = 0
    options = visuals or VisualsConfig()
    for index, lecture in enumerate(course.lectures, start=1):
        lecture_dir = safe_join(course_root, *lecture_relative_dir(lecture).parts)
        try:
            video_path = safe_join(lecture_dir, "video.mp4")
        except ValueError:
            failures.append(
                ExtractFailure(
                    lecture_id=lecture.lecture_id,
                    lecture_name=lecture.lecture_name,
                    message="Downloaded video path is unsafe",
                )
            )
            continue
        logger.info("[%d/%d] %s", index, len(course.lectures), lecture.lecture_name)
        if not video_path.is_file():
            failures.append(
                ExtractFailure(
                    lecture_id=lecture.lecture_id,
                    lecture_name=lecture.lecture_name,
                    message="Downloaded video is missing",
                )
            )
            continue
        transcript_path = _transcript_for(lecture_dir)
        signature = _processing_signature(video_path, transcript_path, language, options)
        if not force and _visuals_are_current(lecture_dir, signature):
            processed += 1
            logger.info("Visuals are up to date; skipping %s", lecture.lecture_name)
            continue
        try:
            extract_lecture(
                video_path,
                transcript_path,
                lecture_dir,
                language=language,
                visuals=options,
            )
            processed += 1
        except Exception:
            failures.append(
                ExtractFailure(
                    lecture_id=lecture.lecture_id,
                    lecture_name=lecture.lecture_name,
                    message="Visual extraction failed for this lecture",
                )
            )
            logger.warning("Visual extraction failed for %s; continuing", lecture.lecture_name)

    summary = ExtractSummary(
        course_name=course.name,
        total_lectures=len(course.lectures),
        processed_lectures=processed,
        failed_lectures=len(failures),
        failures=failures,
    )
    safe_join(course_root, "extract_report.json").write_text(
        summary.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return summary
