"""Normalized course models shared by source adapters and output writers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Lecture(BaseModel):
    model_config = ConfigDict(extra="ignore")

    course_id: str
    module_id: str
    module_index: int = Field(ge=1)
    module_name: str
    module_slug: str
    lesson_id: str | None = None
    lesson_name: str | None = None
    lesson_slug: str | None = None
    lesson_index: int = Field(default=1, ge=1)
    lecture_id: str
    lecture_index: int = Field(ge=1)
    lecture_name: str
    lecture_slug: str
    duration_seconds: float | None = Field(default=None, ge=0)


class Lesson(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str
    slug: str
    index: int = Field(ge=1)
    lectures: list[Lecture] = Field(default_factory=list)


class Module(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    slug: str
    index: int = Field(ge=1)
    lessons: list[Lesson] = Field(default_factory=list)


class Course(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    slug: str
    modules: list[Module] = Field(default_factory=list)

    @property
    def lectures(self) -> list[Lecture]:
        return [
            lecture
            for module in self.modules
            for lesson in module.lessons
            for lecture in lesson.lectures
        ]

    @property
    def duration_seconds(self) -> float:
        return sum(lecture.duration_seconds or 0 for lecture in self.lectures)
