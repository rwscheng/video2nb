from coursera_notes.transcript.parser import parse_timestamped_transcript, timestamp_to_seconds


def test_timestamp_parsing_accepts_srt_vtt_and_hours() -> None:
    assert timestamp_to_seconds("01:02:03,500") == 3723.5
    assert timestamp_to_seconds("02:03.250") == 123.25


def test_timestamped_transcript_preserves_multiline_caption_text() -> None:
    segments = parse_timestamped_transcript(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.500\n"
        "As you can see in this graph,\nthe line decreases.\n"
    )

    assert len(segments) == 1
    assert segments[0].start_seconds == 1.0
    assert segments[0].text == "As you can see in this graph, the line decreases."
