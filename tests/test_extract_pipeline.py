import json
from pathlib import Path

from PIL import Image, ImageDraw

from coursera_notes.config import VisualsConfig
from coursera_notes.pipeline.extract import _limit_candidate_data, extract_lecture
from coursera_notes.video.probe import VideoInfo


def test_extract_lecture_combines_transcript_and_scene_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"placeholder")
    transcript = tmp_path / "subtitles.vtt"
    transcript.write_text(
        "WEBVTT\n\n00:00:10.000 --> 00:00:12.000\n"
        "As you can see in this graph, the result improves.\n"
    )
    output = tmp_path / "lecture"
    captured: list[float] = []

    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.probe_video",
        lambda _: VideoInfo(duration_seconds=30, width=320, height=180, frame_rate=30),
    )
    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.detect_scene_changes",
        lambda *args, **kwargs: [15.0],
    )

    def fake_frame(_: Path, timestamp: float, destination: Path, **kwargs: object) -> Path:
        captured.append(timestamp)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (320, 180), "white")
        draw = ImageDraw.Draw(image)
        x = int(timestamp * 7) % 250
        draw.rectangle((x, 20, x + 30, 160), fill=(20, 70, 180))
        image.save(destination)
        return destination

    monkeypatch.setattr("coursera_notes.pipeline.extract.extract_frame", fake_frame)
    monkeypatch.setattr("coursera_notes.pipeline.extract.is_low_value_image", lambda _: False)
    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.deduplicate_images",
        lambda candidates, max_distance: candidates,
    )

    result = extract_lecture(
        video,
        transcript,
        output,
        language="en",
        visuals=VisualsConfig(cue_window_seconds=1.5, max_visuals_per_hour=60),
    )

    assert sorted(captured) == [0.5, 8.5, 10.0, 11.5, 15.0]
    assert result.candidate_count == 5
    assert result.visual_count == 5
    records = json.loads((output / "visual_manifest.json").read_text())
    cue_record = next(record for record in records if "transcript_cue" in record["sources"])
    assert cue_record["transcript_context"].startswith("As you can see")
    assert cue_record["cue"].casefold() in cue_record["transcript_context"].casefold()
    assert (output / "visual_notes.md").is_file()
    assert len(list((output / "visuals").glob("*.jpg"))) == 5


def test_scene_detection_failure_does_not_discard_transcript_frames(
    tmp_path: Path, monkeypatch
) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"placeholder")
    transcript = tmp_path / "subtitles.srt"
    transcript.write_text("1\n00:00:03,000 --> 00:00:04,000\nLook at this table.\n")
    output = tmp_path / "lecture"
    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.probe_video",
        lambda _: VideoInfo(duration_seconds=8, width=320, height=180, frame_rate=30),
    )

    def broken_scenes(*args: object, **kwargs: object) -> list[float]:
        raise RuntimeError("decoder failure")

    monkeypatch.setattr("coursera_notes.pipeline.extract.detect_scene_changes", broken_scenes)
    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.extract_frame",
        lambda video, time_seconds, destination, **kwargs: _write_frame(destination),
    )
    monkeypatch.setattr("coursera_notes.pipeline.extract.is_low_value_image", lambda _: False)
    monkeypatch.setattr(
        "coursera_notes.pipeline.extract.deduplicate_images",
        lambda candidates, max_distance: candidates,
    )

    result = extract_lecture(video, transcript, output, visuals=VisualsConfig())

    assert result.visual_count == 3
    assert any(
        "transcript_cue" in record["sources"]
        for record in json.loads((output / "visual_manifest.json").read_text())
    )


def test_candidate_preselection_bounds_work_and_prefers_transcript_cues() -> None:
    candidates = {
        10.0: {"sources": {"scene_change"}, "transcript_context": None, "cue": None},
        20.0: {
            "sources": {"transcript_cue"},
            "transcript_context": "Look at this chart",
            "cue": "chart",
        },
        1300.0: {"sources": {"scene_change"}, "transcript_context": None, "cue": None},
        2500.0: {"sources": {"scene_change"}, "transcript_context": None, "cue": None},
        3500.0: {"sources": {"scene_change"}, "transcript_context": None, "cue": None},
    }

    limited = _limit_candidate_data(candidates, duration_seconds=3600, max_visuals_per_hour=1)

    assert len(limited) == 3
    assert 20.0 in limited
    assert 10.0 not in limited


def _write_frame(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), "white").save(destination)
    return destination
