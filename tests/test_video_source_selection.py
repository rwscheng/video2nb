import json
from pathlib import Path

from coursera_notes.coursera.parser import (
    parse_lecture_metadata,
    select_transcript_source,
    select_video_source,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_resolution_prefers_requested_or_highest_below_it() -> None:
    sources = [
        {"resolution": "360p", "url": "360"},
        {"resolution": "540p", "url": "540"},
        {"resolution": "1080p", "url": "1080"},
    ]

    assert select_video_source(sources, "720p").url == "540"
    assert select_video_source(sources, "1440p").url == "1080"
    assert select_video_source(sources, "240p").url == "360"


def test_lecture_metadata_extracts_video_and_subtitle_urls_from_one_payload() -> None:
    payload = json.loads((FIXTURES / "lecture_video.json").read_text())

    metadata = parse_lecture_metadata(payload)

    assert select_video_source(metadata.video_sources, "720p").resolution == "720p"
    assert select_transcript_source(metadata.transcript_sources, "en").format == "txt"
    assert select_transcript_source(metadata.subtitle_sources, "fr").format == "vtt"


def test_lecture_metadata_resolves_relative_coursera_subtitle_urls() -> None:
    metadata = parse_lecture_metadata(
        {
            "linked": {
                "onDemandVideos.v1": [
                    {
                        "id": "course-42~lecture-first",
                        "subtitles": {"en": "/assets/english.vtt?key=example"},
                        "subtitlesTxt": {"en": "/assets/english.txt?key=example"},
                    }
                ]
            }
        }
    )

    transcript = select_transcript_source(metadata.transcript_sources, "en")
    subtitle = select_transcript_source(metadata.subtitle_sources, "en")

    assert transcript is not None
    assert transcript.url == "https://www.coursera.org/assets/english.txt?key=example"
    assert transcript.format == "txt"
    assert subtitle is not None
    assert subtitle.url == "https://www.coursera.org/assets/english.vtt?key=example"
