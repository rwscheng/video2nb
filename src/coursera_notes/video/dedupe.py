"""Perceptual image deduplication and inexpensive screenshot quality checks."""

from __future__ import annotations

from pathlib import Path

import imagehash
from PIL import Image, ImageFilter, ImageStat

from coursera_notes.video.models import ScreenshotCandidate


def image_quality_score(path: Path) -> float:
    """Return a cheap edge-detail score after shrinking to comparison size."""
    with Image.open(path) as source:
        image = source.convert("L")
        image.thumbnail((256, 144))
        edges = image.filter(ImageFilter.FIND_EDGES)
        return ImageStat.Stat(edges).var[0]


def is_low_value_image(path: Path) -> bool:
    """Reject nearly black frames and bright blank transition frames."""
    with Image.open(path) as source:
        image = source.convert("L")
        image.thumbnail((128, 72))
        stats = ImageStat.Stat(image)
        mean = stats.mean[0]
        spread = stats.stddev[0]
    return mean < 5 or (mean > 249 and spread < 3)


def _small_hash(path: Path) -> imagehash.ImageHash:
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((256, 144))
        return imagehash.phash(image)


def _priority(candidate: ScreenshotCandidate) -> tuple[int, int, float, float]:
    return (
        int("transcript_cue" in candidate.sources),
        int("scene_change" in candidate.sources),
        candidate.quality_score,
        -candidate.timestamp_seconds,
    )


def deduplicate_images(
    candidates: list[ScreenshotCandidate], max_distance: int = 6
) -> list[ScreenshotCandidate]:
    """Keep preferred representatives and merge provenance for near-duplicates."""
    prepared = [
        candidate.model_copy(deep=True) for candidate in candidates if candidate.path.is_file()
    ]
    for candidate in prepared:
        if candidate.quality_score <= 0:
            candidate.quality_score = image_quality_score(candidate.path)
    prepared.sort(key=_priority, reverse=True)

    kept: list[ScreenshotCandidate] = []
    hashes: list[imagehash.ImageHash] = []
    for candidate in prepared:
        candidate_hash = _small_hash(candidate.path)
        matches = [
            (candidate_hash - kept_hash, index)
            for index, kept_hash in enumerate(hashes)
            if candidate_hash - kept_hash <= max_distance
        ]
        if not matches:
            kept.append(candidate)
            hashes.append(candidate_hash)
            continue

        _, match_index = min(matches, key=lambda item: item[0])
        representative = kept[match_index]
        merged_sources = representative.sources | candidate.sources
        if _priority(candidate) > _priority(representative):
            candidate.sources = merged_sources
            candidate.transcript_context = (
                candidate.transcript_context or representative.transcript_context
            )
            candidate.cue = candidate.cue or representative.cue
            kept[match_index] = candidate
            hashes[match_index] = candidate_hash
        else:
            representative.sources = merged_sources
            representative.transcript_context = (
                representative.transcript_context or candidate.transcript_context
            )
            representative.cue = representative.cue or candidate.cue
    return sorted(kept, key=lambda candidate: candidate.timestamp_seconds)
