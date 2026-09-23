"""Normalized transcript segments and visual-reference cues."""

from pydantic import BaseModel, ConfigDict, Field


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str


class VisualCue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timestamp_seconds: float = Field(ge=0)
    context: str
    cue: str
