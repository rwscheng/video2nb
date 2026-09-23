"""Defensive parsing for Coursera's linked-data course and lecture APIs."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from typing import Any
from urllib.parse import urljoin

from coursera_notes.coursera.models import LectureMetadata, SubtitleSource, VideoSource
from coursera_notes.models import Course, Lecture, Lesson, Module

_MODULE_COLLECTIONS = ("onDemandCourseModules.v1", "onDemandCourseModules.v2", "modules")
_LESSON_COLLECTIONS = ("onDemandCourseLessons.v1", "onDemandCourseLessons.v2", "lessons")
_ITEM_COLLECTIONS = (
    "onDemandCourseMaterialItems.v2",
    "onDemandCourseMaterialItems.v1",
    "courseMaterialItems",
    "items",
)
_RESOLUTION_RE = re.compile(r"(\d{3,4})\s*p", re.IGNORECASE)
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*$", re.IGNORECASE)
_COURSERA_BASE_URL = "https://www.coursera.org/"


def _dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _dicts(child)


def _collect_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def add(value: Any, collection: str = "") -> None:
        if isinstance(value, list):
            for child in value:
                add(child, collection)
            return
        if not isinstance(value, dict):
            return
        identity = value.get("id")
        if identity is not None and any(
            key in value for key in ("name", "slug", "contentSummary", "moduleIds", "lessonIds")
        ):
            marker = (str(identity), id(value))
            if marker not in seen:
                seen.add(marker)
                record = dict(value)
                if collection:
                    record["__collection"] = collection
                records.append(record)
        for key in ("elements", "linked", "modules", "lessons", "items", "courseMaterialItems"):
            if key in value:
                add(value[key], key if key != "linked" else collection)
        if collection == "linked":
            for key, child in value.items():
                if isinstance(child, (list, dict)):
                    add(child, str(key))

    add(payload.get("elements", []), "elements")
    add(payload.get("linked", {}), "linked")
    if not records and payload.get("id"):
        add(dict(payload), "")
    return records


def _key_ids(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            item = (
                item.get("id") or item.get("itemId") or item.get("moduleId") or item.get("lessonId")
            )
        if item is not None:
            result.append(str(item))
    return result


def _refs(record: Mapping[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = record.get(key)
        if value is None and isinstance(record.get("linked"), Mapping):
            value = record["linked"].get(key)
        if value is not None:
            return value if isinstance(value, list) else [value]
    return []


def _slug(value: Any, fallback: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return result or fallback


def _duration(record: Mapping[str, Any]) -> float | None:
    sources = [record]
    if isinstance(record.get("contentSummary"), Mapping):
        sources.append(record["contentSummary"])
    for source in sources:
        for key in ("duration", "durationSeconds", "durationInSeconds", "videoDuration"):
            value = source.get(key)
            if isinstance(value, Mapping):
                value = value.get("seconds") or value.get("value")
            try:
                if value is not None:
                    seconds = float(value)
                    return seconds if seconds >= 0 else None
            except (TypeError, ValueError):
                continue
    return None


def _is_lecture(record: Mapping[str, Any]) -> bool:
    summary = record.get("contentSummary")
    type_name = summary.get("typeName") if isinstance(summary, Mapping) else None
    return str(type_name or record.get("typeName") or "").casefold() == "lecture"


def _is_locked(record: Mapping[str, Any]) -> bool:
    summary = record.get("contentSummary")
    flags: list[Any] = [record.get("isLocked"), record.get("locked")]
    if isinstance(summary, Mapping):
        flags.extend([summary.get("isLocked"), summary.get("locked")])
        if summary.get("isAccessible") is False or summary.get("isAvailable") is False:
            return True
    return any(flag is True for flag in flags) or any(
        record.get(key) is False for key in ("isAccessible", "isAvailable")
    )


def _records_from_refs(
    refs: Iterable[Any],
    by_id: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for ref in refs:
        record: dict[str, Any] | None
        if isinstance(ref, Mapping) and ref.get("id") is not None:
            record = by_id.get(str(ref["id"])) or {str(key): value for key, value in ref.items()}
        else:
            ids = _key_ids(ref)
            record = by_id.get(ids[0]) if ids else None
        if record is not None:
            result.append(record)
    return result


def _named_refs(
    owner: Mapping[str, Any], keys: tuple[str, ...], by_id: Mapping[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    return _records_from_refs(_refs(owner, *keys), by_id)


def _fallback_children(
    records: list[dict[str, Any]],
    collection_fragments: tuple[str, ...],
    parent_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for record in records:
        collection = str(record.get("__collection", "")).casefold()
        parent_values: set[str] = set()
        for key in ("parentId", "parent", "courseId", "moduleId", "lessonId"):
            parent = record.get(key)
            if isinstance(parent, Mapping):
                parent = parent.get("id")
            if parent is not None:
                parent_values.add(str(parent))
        type_name = str(record.get("typeName") or "").casefold()
        if (
            any(fragment.casefold() in collection for fragment in collection_fragments)
            or type_name in collection_fragments
        ):
            if not parent_values or parent_values.intersection(parent_ids):
                matches.append(record)
    return matches


def parse_course_materials(payload: Mapping[str, Any], requested_slug: str | None = None) -> Course:
    """Normalize course, module, lesson and lecture ordering from an API response."""
    records = _collect_records(payload)
    by_id = {str(record["id"]): record for record in records if record.get("id") is not None}
    courses = [
        record
        for record in records
        if record.get("slug") or record.get("moduleIds") or record.get("modules")
    ]
    course_record = next(
        (record for record in courses if requested_slug and record.get("slug") == requested_slug),
        courses[0] if courses else None,
    )
    if course_record is None:
        raise ValueError("Course materials response did not contain a course")

    module_records = _named_refs(course_record, ("moduleIds", "modules", "moduleId"), by_id)
    if not module_records:
        module_records = _fallback_children(
            records,
            ("module",),
            (str(course_record.get("id", "")),),
        )

    modules: list[Module] = []
    for module_index, module_record in enumerate(module_records, start=1):
        module_id = str(module_record.get("id") or f"module-{module_index}")
        lesson_records = _named_refs(module_record, ("lessonIds", "lessons", "lessonId"), by_id)
        if not lesson_records:
            lesson_records = _fallback_children(records, ("lesson",), (module_id,))

        lessons: list[Lesson] = []
        module_lecture_index = 0
        for lesson_index, lesson_record in enumerate(lesson_records, start=1):
            lesson_id_value = lesson_record.get("id")
            lesson_id = str(lesson_id_value) if lesson_id_value is not None else None
            item_records = _named_refs(
                lesson_record,
                (
                    "itemIds",
                    "elementIds",
                    "courseMaterialItemIds",
                    "courseMaterialIds",
                    "materialItemIds",
                    "items",
                ),
                by_id,
            )
            if not item_records:
                item_records = _fallback_children(
                    records, ("materialitem", "coursematerialitem"), (lesson_id or "",)
                )
            lectures: list[Lecture] = []
            for item_record in item_records:
                if not _is_lecture(item_record) or _is_locked(item_record):
                    continue
                module_lecture_index += 1
                lecture_id = str(item_record.get("id") or f"lecture-{module_lecture_index}")
                lecture_name = str(item_record.get("name") or f"Lecture {module_lecture_index}")
                lectures.append(
                    Lecture(
                        course_id=str(course_record.get("id", "")),
                        module_id=module_id,
                        module_index=module_index,
                        module_name=str(module_record.get("name") or f"Module {module_index}"),
                        module_slug=_slug(
                            module_record.get("slug") or module_record.get("name"),
                            f"module-{module_index}",
                        ),
                        lesson_id=lesson_id,
                        lesson_name=str(lesson_record.get("name") or f"Lesson {lesson_index}"),
                        lesson_slug=_slug(
                            lesson_record.get("slug") or lesson_record.get("name"),
                            f"lesson-{lesson_index}",
                        ),
                        lesson_index=lesson_index,
                        lecture_id=lecture_id,
                        lecture_index=module_lecture_index,
                        lecture_name=lecture_name,
                        lecture_slug=_slug(
                            item_record.get("slug") or lecture_name,
                            f"lecture-{module_lecture_index}",
                        ),
                        duration_seconds=_duration(item_record),
                    )
                )
            lesson_name = str(lesson_record.get("name") or f"Lesson {lesson_index}")
            lessons.append(
                Lesson(
                    id=lesson_id,
                    name=lesson_name,
                    slug=_slug(lesson_record.get("slug") or lesson_name, f"lesson-{lesson_index}"),
                    index=lesson_index,
                    lectures=lectures,
                )
            )
        module_name = str(module_record.get("name") or f"Module {module_index}")
        modules.append(
            Module(
                id=module_id,
                name=module_name,
                slug=_slug(module_record.get("slug") or module_name, f"module-{module_index}"),
                index=module_index,
                lessons=lessons,
            )
        )

    name = str(
        course_record.get("name") or course_record.get("title") or requested_slug or "Course"
    )
    return Course(
        id=str(course_record.get("id") or "unknown-course"),
        name=name,
        slug=_slug(course_record.get("slug") or requested_slug or name, "course"),
        modules=modules,
    )


def _resolution_number(value: Any) -> int | None:
    match = _RESOLUTION_RE.search(str(value or ""))
    return int(match.group(1)) if match else None


def _url_in(value: Any) -> str | None:
    if isinstance(value, str):
        if value.startswith(("https://", "http://")):
            return value
        if value.startswith("/") and not value.startswith("//"):
            return urljoin(_COURSERA_BASE_URL, value)
        return None
    if isinstance(value, Mapping):
        for key in ("mp4VideoUrl", "url", "src", "downloadUrl", "videoUrl"):
            found = value.get(key)
            if isinstance(found, str) and found.startswith(("https://", "http://")):
                return found
        for child in value.values():
            found = _url_in(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _url_in(child)
            if found:
                return found
    return None


def _parse_video_sources(payload: Mapping[str, Any]) -> list[VideoSource]:
    found: dict[tuple[str, str], VideoSource] = {}
    for record in _dicts(dict(payload)):
        if record.get("resolution") and _url_in(record):
            number = _resolution_number(record["resolution"])
            url = _url_in(record)
            if number and url:
                found[(f"{number}p", url)] = VideoSource(resolution=f"{number}p", url=url)
        for key, value in record.items():
            resolution = _resolution_number(key)
            if resolution:
                url = _url_in(value)
                if url:
                    found[(f"{resolution}p", url)] = VideoSource(
                        resolution=f"{resolution}p", url=url
                    )
    return list(found.values())


def _language(record: Mapping[str, Any], fallback: str = "") -> str:
    for key in ("languageCode", "language", "lang", "locale", "localeCode"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value.replace("_", "-")
    return fallback


def _parse_subtitle_values(value: Any, format_hint: str) -> list[SubtitleSource]:
    output: list[SubtitleSource] = []

    def visit(item: Any, inherited_language: str = "") -> None:
        if isinstance(item, list):
            for child in item:
                visit(child, inherited_language)
            return
        if isinstance(item, Mapping):
            language = _language(item, inherited_language)
            url = _url_in(item)
            if url:
                suffix_match = re.search(r"\.(txt|srt|vtt)(?:$|\?)", url, re.IGNORECASE)
                file_format = suffix_match.group(1).lower() if suffix_match else format_hint
                if language:
                    output.append(SubtitleSource(language=language, url=url, format=file_format))
                    return
            for key, child in item.items():
                next_language = key if _LANGUAGE_RE.fullmatch(str(key)) else language
                visit(child, next_language)
            return
        if isinstance(item, str) and inherited_language:
            url = _url_in(item)
            if not url:
                return
            suffix_match = re.search(r"\.(txt|srt|vtt)(?:$|\?)", url, re.IGNORECASE)
            file_format = suffix_match.group(1).lower() if suffix_match else format_hint
            output.append(
                SubtitleSource(language=inherited_language, url=url, format=file_format)
            )

    visit(value)
    return output


def parse_lecture_metadata(payload: Mapping[str, Any]) -> LectureMetadata:
    video_sources = _parse_video_sources(payload)
    subtitles: list[SubtitleSource] = []
    transcripts: list[SubtitleSource] = []
    for record in _dicts(dict(payload)):
        if "subtitlesTxt" in record:
            transcripts.extend(_parse_subtitle_values(record["subtitlesTxt"], "txt"))
        if "subtitles" in record:
            subtitles.extend(_parse_subtitle_values(record["subtitles"], "vtt"))
        if "dubbedSubtitlesVtt" in record:
            subtitles.extend(_parse_subtitle_values(record["dubbedSubtitlesVtt"], "vtt"))
    return LectureMetadata(
        video_sources=_dedupe_video_sources(video_sources),
        subtitle_sources=_dedupe_sources(subtitles),
        transcript_sources=_dedupe_sources(transcripts),
    )


def _dedupe_sources(sources: list[SubtitleSource]) -> list[SubtitleSource]:
    unique: dict[tuple[str, str, str], SubtitleSource] = {}
    for source in sources:
        unique[(source.language.casefold(), source.format, source.url)] = source
    return list(unique.values())


def _dedupe_video_sources(sources: list[VideoSource]) -> list[VideoSource]:
    unique: dict[tuple[str, str], VideoSource] = {}
    for source in sources:
        unique[(source.resolution.casefold(), source.url)] = source
    return list(unique.values())


def select_video_source(
    sources: Iterable[VideoSource | Mapping[str, Any]], requested_resolution: str
) -> VideoSource | None:
    normalized: list[VideoSource] = []
    for source in sources:
        try:
            item = source if isinstance(source, VideoSource) else VideoSource.model_validate(source)
        except (ValueError, TypeError):
            continue
        if _resolution_number(item.resolution):
            normalized.append(item)
    if not normalized:
        return None
    target = _resolution_number(requested_resolution) or 720
    below = [
        source for source in normalized if (_resolution_number(source.resolution) or 0) <= target
    ]
    if below:
        return max(below, key=lambda source: _resolution_number(source.resolution) or 0)
    return min(
        normalized,
        key=lambda source: abs((_resolution_number(source.resolution) or 0) - target),
    )


def select_transcript_source(
    sources: Iterable[SubtitleSource | Mapping[str, Any]], language: str
) -> SubtitleSource | None:
    normalized: list[SubtitleSource] = []
    for source in sources:
        try:
            normalized.append(
                source
                if isinstance(source, SubtitleSource)
                else SubtitleSource.model_validate(source)
            )
        except (ValueError, TypeError):
            continue
    requested = language.casefold().replace("_", "-")
    exact = [
        source for source in normalized if source.language.casefold().replace("_", "-") == requested
    ]
    if exact:
        return exact[0]
    base = requested.split("-", 1)[0]
    return next(
        (source for source in normalized if source.language.casefold().split("-", 1)[0] == base),
        None,
    )
