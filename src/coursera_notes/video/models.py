"""Intermediate and final visual metadata models."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ScreenshotCandidate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    timestamp_seconds: float = Field(ge=0)
    path: Path
    sources: set[str] = Field(default_factory=set)
    transcript_context: str | None = None
    cue: str | None = None
    quality_score: float = 0


class VisualRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timestamp_seconds: float = Field(ge=0)
    timestamp: str
    filename: str
    sources: list[str]
    transcript_context: str | None = None
    cue: str | None = None
