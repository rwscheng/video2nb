from coursera_notes.transcript.models import TranscriptSegment
from coursera_notes.transcript.visual_cues import match_visual_cues


def test_default_visual_cues_find_context_and_phrase() -> None:
    segments = [
        TranscriptSegment(start_seconds=12, end_seconds=15, text="The model converges."),
        TranscriptSegment(
            start_seconds=16, end_seconds=20, text="As you can see in this graph, the loss drops."
        ),
    ]

    cues = match_visual_cues(segments, language="en")

    assert len(cues) == 1
    assert cues[0].timestamp_seconds == 16
    assert cues[0].context == "As you can see in this graph, the loss drops."
    assert cues[0].cue.casefold() in cues[0].context.casefold()


def test_cue_patterns_can_be_extended_for_another_language() -> None:
    cues = match_visual_cues(
        [TranscriptSegment(start_seconds=4, end_seconds=6, text="Regardez ce diagramme.")],
        language="fr",
        extra_patterns={"fr": ["regardez ce diagramme"]},
    )

    assert len(cues) == 1
