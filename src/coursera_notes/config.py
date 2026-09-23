"""Typed project configuration loaded from an optional TOML file."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CourseraConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resolution: str = "720p"
    language: str = "en"
    workers: int = Field(default=2, ge=1, le=8)


class VisualsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scene_threshold: float = Field(default=30.0, ge=0)
    hash_distance: int = Field(default=6, ge=0, le=64)
    cue_window_seconds: float = Field(default=1.5, ge=0)
    jpeg_quality: int = Field(default=88, ge=1, le=100)
    max_visuals_per_hour: int = Field(default=60, ge=1)
    minimum_scene_seconds: float = Field(default=1.5, gt=0)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    root: Path = Path("output")

    @field_validator("root", mode="before")
    @classmethod
    def expand_root(cls, value: Any) -> Path:
        return Path(value).expanduser()


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    coursera: CourseraConfig = Field(default_factory=CourseraConfig)
    visuals: VisualsConfig = Field(default_factory=VisualsConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_config(path: Path | None = None) -> AppConfig:
    """Read the project config, falling back to V1 defaults when absent."""
    config_path = path or (Path.cwd() / "coursera-notes.toml")
    if not config_path.is_file():
        return AppConfig()
    with config_path.open("rb") as stream:
        values = tomllib.load(stream)
    return AppConfig.model_validate(values)
