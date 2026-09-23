"""Language-extensible phrase matching for transcript-guided frame selection."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from re import Pattern

from coursera_notes.transcript.models import TranscriptSegment, VisualCue

# Add phrase sets here as more transcript languages are needed. Matching is
# case-insensitive and returns the longest phrase found in each caption.
CUE_PATTERNS: dict[str, tuple[str, ...]] = {
    "en": (
        "as you can see",
        "as shown",
        "shown here",
        "this diagram",
        "this graph",
        "this chart",
        "this table",
        "this equation",
        "this formula",
        "on the screen",
        "here you can see",
        "this code",
        "this example",
        "look at",
        "notice here",
        "in this figure",
        "this slide",
    ),
    "es": (
        "como pueden ver",
        "como se muestra",
        "en esta gráfica",
        "en este gráfico",
        "esta ecuación",
        "en la pantalla",
        "miren aquí",
        "esta diapositiva",
    ),
    "fr": (
        "comme vous pouvez le voir",
        "comme indiqué",
        "ce diagramme",
        "ce graphique",
        "cette équation",
        "à l'écran",
        "regardez ici",
        "cette diapositive",
    ),
}


def match_visual_cues(
    segments: Iterable[TranscriptSegment],
    language: str = "en",
    extra_patterns: Mapping[str, Iterable[str | Pattern[str]]] | None = None,
) -> list[VisualCue]:
    """Return one strongest phrase match per caption, preserving caption time/context."""
    language_key = language.casefold().replace("_", "-").split("-", 1)[0]
    phrases: list[str | Pattern[str]] = list(CUE_PATTERNS.get(language_key, ()))
    if extra_patterns:
        phrases.extend(extra_patterns.get(language, extra_patterns.get(language_key, ())))
    patterns: list[tuple[int, re.Pattern[str], str | None]] = []
    for phrase in phrases:
        if isinstance(phrase, re.Pattern):
            patterns.append((len(phrase.pattern), phrase, None))
        else:
            patterns.append(
                (
                    len(phrase),
                    re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE),
                    phrase,
                )
            )
    patterns.sort(key=lambda item: item[0], reverse=True)

    cues: list[VisualCue] = []
    for segment in segments:
        for _, pattern, literal in patterns:
            match = pattern.search(segment.text)
            if match:
                phrase = match.group(0) if literal is None else match.group(0)
                cues.append(
                    VisualCue(
                        timestamp_seconds=segment.start_seconds,
                        context=segment.text,
                        cue=phrase,
                    )
                )
                break
    return cues
