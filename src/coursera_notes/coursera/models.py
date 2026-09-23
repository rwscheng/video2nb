"""Coursera response models that keep raw API JSON at the adapter edge."""

from pydantic import BaseModel, ConfigDict


class VideoSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolution: str
    url: str


class SubtitleSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str
    url: str
    format: str


class LectureMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video_sources: list[VideoSource]
    subtitle_sources: list[SubtitleSource]
    transcript_sources: list[SubtitleSource]

    @property
    def subtitle_languages(self) -> list[str]:
        return sorted(
            {source.language for source in self.subtitle_sources + self.transcript_sources}
        )


class FetchFailure(BaseModel):
    lecture_id: str
    lecture_name: str
    kind: str
    message: str
