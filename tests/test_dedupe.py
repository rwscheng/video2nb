from pathlib import Path

from PIL import Image

from coursera_notes.video.dedupe import deduplicate_images
from coursera_notes.video.models import ScreenshotCandidate


def test_dedupe_keeps_transcript_candidate_and_merges_sources(tmp_path: Path) -> None:
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    image = Image.new("RGB", (320, 180), "white")
    for x in range(40, 280, 30):
        for y in range(30, 150, 20):
            image.putpixel((x, y), (10, 20, 30))
    image.save(first)
    image.save(second)
    candidates = [
        ScreenshotCandidate(timestamp_seconds=20, path=second, sources={"scene_change"}),
        ScreenshotCandidate(
            timestamp_seconds=19, path=first, sources={"transcript_cue"}, cue="this diagram"
        ),
    ]

    kept = deduplicate_images(candidates, max_distance=0)

    assert len(kept) == 1
    assert kept[0].path == first
    assert kept[0].sources == {"scene_change", "transcript_cue"}
