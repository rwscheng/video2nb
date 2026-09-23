"""Parse timestamped SRT/VTT captions without adding a subtitle dependency."""

from __future__ import annotations

import html
import re

from coursera_notes.transcript.models import TranscriptSegment

_TIMESTAMP = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
_CAPTION_LINE = re.compile(rf"^\s*({_TIMESTAMP})\s*-->\s*({_TIMESTAMP})", re.MULTILINE)
_PLAIN_LINE = re.compile(rf"^\s*({_TIMESTAMP})\s+(.+?)\s*$", re.MULTILINE)
_TAG = re.compile(r"<[^>]*>")


def timestamp_to_seconds(value: str) -> float:
    """Convert HH:MM:SS, MM:SS, or seconds timestamps into seconds."""
    normalized = value.strip().replace(",", ".")
    parts = normalized.split(":")
    try:
        seconds = float(parts[-1])
        if len(parts) == 1:
            return seconds
        minutes = int(parts[-2])
        hours = int(parts[-3]) if len(parts) >= 3 else 0
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Invalid subtitle timestamp: {value!r}") from exc
    if minutes > 59 or (len(parts) >= 3 and int(parts[-2]) > 59):
        raise ValueError(f"Invalid subtitle timestamp: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def _clean_caption(text: str) -> str:
    text = html.unescape(_TAG.sub("", text))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_timestamped_transcript(text: str) -> list[TranscriptSegment]:
    """Parse SRT/VTT cue blocks and common timestamp-prefixed TXT transcripts."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[TranscriptSegment] = []
    index = 0
    while index < len(lines):
        match = _CAPTION_LINE.match(lines[index])
        if match:
            start = timestamp_to_seconds(match.group(1))
            end = timestamp_to_seconds(match.group(2))
            index += 1
            caption_lines: list[str] = []
            while index < len(lines) and lines[index].strip():
                if _CAPTION_LINE.match(lines[index]):
                    break
                caption_lines.append(lines[index].strip())
                index += 1
            caption = _clean_caption(" ".join(caption_lines))
            if caption and end >= start:
                segments.append(
                    TranscriptSegment(start_seconds=start, end_seconds=end, text=caption)
                )
            continue
        plain = _PLAIN_LINE.match(lines[index])
        if plain:
            start = timestamp_to_seconds(plain.group(1))
            caption = _clean_caption(plain.group(2))
            if caption:
                segments.append(
                    TranscriptSegment(start_seconds=start, end_seconds=start, text=caption)
                )
        index += 1
    return segments
