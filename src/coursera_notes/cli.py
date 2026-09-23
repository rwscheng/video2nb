"""Typer CLI entry point."""

from __future__ import annotations

import json
import os
import random
import shutil
import time
from collections import Counter
from pathlib import Path

import typer

from coursera_notes.config import AppConfig, load_config
from coursera_notes.coursera.client import CourseraAPIError, CourseraClient
from coursera_notes.logging import configure_logging
from coursera_notes.models import Course
from coursera_notes.output.notebooklm import package_notebooklm
from coursera_notes.output.paths import safe_component, safe_join
from coursera_notes.pipeline.extract import (
    ExtractSummary,
    extract_course,
    extract_lecture,
)
from coursera_notes.pipeline.fetch import FetchSummary, fetch_course

app = typer.Typer(
    name="coursera-notes",
    help="Turn authorized Coursera courses and local videos into NotebookLM study packages.",
    no_args_is_help=True,
)


@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed progress."),
    quiet: bool = typer.Option(False, "--quiet", help="Show errors and final summaries only."),
) -> None:
    configure_logging(verbose=verbose, quiet=quiet)


def _client(cauth: str | None) -> CourseraClient:
    value = cauth or os.environ.get("COURSERA_CAUTH")
    if not value:
        typer.echo("Set COURSERA_CAUTH or pass --cauth.", err=True)
        raise typer.Exit(code=2)
    try:
        return CourseraClient(value)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None


def _config() -> AppConfig:
    try:
        return load_config()
    except Exception as exc:
        typer.echo(f"Invalid coursera-notes.toml: {exc}", err=True)
        raise typer.Exit(code=2) from None


def _root(output: Path | None, config: AppConfig) -> Path:
    path = (output or config.output.root).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _client_error(exc: CourseraAPIError) -> None:
    typer.echo(f"Coursera request failed: {exc}", err=True)
    raise typer.Exit(code=1) from None


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes <= 0:
        return "unknown"
    hours, remainder = divmod(minutes, 60)
    return f"{hours} hr {remainder} min" if hours else f"{remainder} min"


def _print_fetch_summary(summary: FetchSummary) -> None:
    typer.echo(f"{summary.total_lectures} lectures processed")
    typer.echo(f"{summary.complete_lectures} complete")
    if summary.failed_lectures:
        counts = Counter(failure.kind for failure in summary.failures)
        transcript_issues = sum(
            counts[key] for key in ("transcript_missing", "transcript_failed", "subtitle_failed")
        )
        video_issues = counts["video_missing"] + counts["video_failed"]
        if transcript_issues:
            typer.echo(f"{transcript_issues} transcript/subtitle issue(s)")
        if video_issues:
            typer.echo(f"{video_issues} video issue(s)")
        if counts["metadata_failed"] or counts["lecture_failed"]:
            issue_count = counts["metadata_failed"] + counts["lecture_failed"]
            typer.echo(f"{issue_count} lecture metadata/processing issue(s)")
        typer.echo(f"Output: {summary.course_dir}")


def _print_extract_summary(summary: ExtractSummary) -> None:
    typer.echo(
        f"{summary.processed_lectures}/{summary.total_lectures} lectures extracted; "
        f"{summary.failed_lectures} failed"
    )


@app.command("inspect")
def inspect_course(
    slug: str = typer.Argument(..., help="Coursera course slug, such as machine-learning."),
    cauth: str | None = typer.Option(
        None, "--cauth", help="CAUTH token or complete Cookie header."
    ),
) -> None:
    """Inspect course structure, duration, and sample transcript languages."""
    client = _client(cauth)
    try:
        with client:
            course = client.get_course(slug)
            languages: set[str] = set()
            for index, lecture in enumerate(course.lectures[:10]):
                if index:
                    time.sleep(0.2 + random.uniform(0, 0.2))
                try:
                    metadata = client.get_lecture_metadata(lecture.course_id, lecture.lecture_id)
                except CourseraAPIError:
                    continue
                languages.update(metadata.subtitle_languages)
    except CourseraAPIError as exc:
        _client_error(exc)
    typer.echo(f"Course: {course.name}")
    typer.echo(f"Modules: {len(course.modules)}")
    typer.echo(f"Lectures: {len(course.lectures)}")
    typer.echo(f"Estimated video duration: {_duration(course.duration_seconds)}")
    language_value = ", ".join(sorted(languages)) if languages else "unavailable"
    sample_suffix = " (sampled from up to 10 lectures)" if len(course.lectures) > 10 else ""
    typer.echo(f"Available transcript languages: {language_value}{sample_suffix}")


@app.command("fetch")
def fetch_command(
    slug: str = typer.Argument(..., help="Coursera course slug."),
    resolution: str | None = typer.Option(
        None, "--resolution", help="Preferred video quality, such as 720p."
    ),
    language: str | None = typer.Option(None, "--language", help="Transcript language code."),
    output: Path | None = typer.Option(None, "--output", help="Output root directory."),
    workers: int | None = typer.Option(
        None, "--workers", min=1, max=8, help="Concurrent lecture workers."
    ),
    video: bool = typer.Option(True, "--video/--no-video", help="Download MP4 videos."),
    transcript: bool = typer.Option(
        True, "--transcript/--no-transcript", help="Download official transcript/subtitles."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace completed files."),
    cauth: str | None = typer.Option(
        None, "--cauth", help="CAUTH token or complete Cookie header."
    ),
) -> None:
    """Download authorized course videos and official transcript files."""
    config = _config()
    effective_resolution = resolution or config.coursera.resolution
    effective_language = language or config.coursera.language
    effective_workers = workers or config.coursera.workers
    client = _client(cauth)
    try:
        with client:
            course = client.get_course(slug)
            summary = fetch_course(
                client,
                course,
                _root(output, config),
                resolution=effective_resolution,
                language=effective_language,
                workers=effective_workers,
                video=video,
                transcript=transcript,
                overwrite=overwrite,
            )
    except CourseraAPIError as exc:
        _client_error(exc)
    _print_fetch_summary(summary)
    if summary.failed_lectures:
        raise typer.Exit(code=1)
    typer.echo(f"Output: {summary.course_dir}")


@app.command("extract")
def extract_command(
    course_dir: Path = typer.Argument(..., help="Existing course output directory."),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Recreate visuals even when current."
    ),
) -> None:
    """Extract and deduplicate screenshots from downloaded lecture videos."""
    config = _config()
    try:
        course_file = safe_join(course_dir, "course.json")
    except ValueError:
        course_file = None
    if course_file is None or not course_file.is_file():
        typer.echo("Course directory must contain course.json; run fetch first.", err=True)
        raise typer.Exit(code=2)
    try:
        summary = extract_course(
            course_dir,
            visuals=config.visuals,
            language=config.coursera.language,
            force=overwrite,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not extract course visuals: {exc}", err=True)
        raise typer.Exit(code=1) from None
    _print_extract_summary(summary)
    if summary.failed_lectures:
        raise typer.Exit(code=1)


@app.command("package")
def package_command(
    course_dir: Path = typer.Argument(..., help="Existing course output directory."),
) -> None:
    """Create a compact NotebookLM folder without copying video files."""
    try:
        course_file = safe_join(course_dir, "course.json")
    except ValueError:
        course_file = None
    if course_file is None or not course_file.is_file():
        typer.echo("Course directory must contain course.json; run fetch first.", err=True)
        raise typer.Exit(code=2)
    try:
        course = Course.model_validate_json(course_file.read_text(encoding="utf-8"))
        package = package_notebooklm(course_dir, course)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not build NotebookLM package: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"NotebookLM package: {package}")


@app.command("build")
def build_command(
    slug: str = typer.Argument(..., help="Coursera course slug."),
    resolution: str | None = typer.Option(None, "--resolution", help="Preferred video quality."),
    language: str | None = typer.Option(None, "--language", help="Transcript language code."),
    output: Path | None = typer.Option(None, "--output", help="Output root directory."),
    workers: int | None = typer.Option(
        None, "--workers", min=1, max=8, help="Concurrent lecture workers."
    ),
    video: bool = typer.Option(True, "--video/--no-video", help="Download MP4 videos."),
    transcript: bool = typer.Option(
        True, "--transcript/--no-transcript", help="Download official transcript/subtitles."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace completed files."),
    cauth: str | None = typer.Option(
        None, "--cauth", help="CAUTH token or complete Cookie header."
    ),
) -> None:
    """Fetch, extract, and package a course, reusing completed local files."""
    config = _config()
    effective_resolution = resolution or config.coursera.resolution
    effective_language = language or config.coursera.language
    effective_workers = workers or config.coursera.workers
    client = _client(cauth)
    try:
        with client:
            course = client.get_course(slug)
            fetched = fetch_course(
                client,
                course,
                _root(output, config),
                resolution=effective_resolution,
                language=effective_language,
                workers=effective_workers,
                video=video,
                transcript=transcript,
                overwrite=overwrite,
            )
    except CourseraAPIError as exc:
        _client_error(exc)
    extracted: ExtractSummary | None = None
    if video:
        extracted = extract_course(
            fetched.course_dir,
            visuals=config.visuals,
            language=effective_language,
            force=overwrite,
        )
    package = package_notebooklm(fetched.course_dir, course)
    _print_fetch_summary(fetched)
    if extracted is not None:
        _print_extract_summary(extracted)
    else:
        typer.echo("Visual extraction skipped (--no-video).")
    typer.echo(f"NotebookLM package: {package}")
    if fetched.failed_lectures or (extracted is not None and extracted.failed_lectures):
        raise typer.Exit(code=1)


@app.command("local")
def local_command(
    video_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    transcript_path: Path | None = typer.Option(
        None,
        "--transcript",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Local SRT, VTT, or timestamped TXT.",
    ),
    output: Path | None = typer.Option(None, "--output", help="Output root directory."),
    language: str | None = typer.Option(None, "--language", help="Transcript language code."),
) -> None:
    """Run the shared visual pipeline on a local video and optional transcript."""
    config = _config()
    effective_language = language or config.coursera.language
    transcript_copy: Path | None = None
    try:
        output_dir = safe_join(_root(output, config), safe_component(video_path.stem))
        output_dir.mkdir(parents=True, exist_ok=True)
        if transcript_path is not None:
            suffix = transcript_path.suffix.casefold()
            target_name = (
                "transcript.txt"
                if suffix == ".txt"
                else f"subtitles{suffix if suffix in {'.srt', '.vtt'} else '.txt'}"
            )
            transcript_copy = safe_join(output_dir, target_name)
            if transcript_path.resolve() != transcript_copy.resolve():
                shutil.copyfile(transcript_path, transcript_copy)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not prepare local video output: {exc}", err=True)
        raise typer.Exit(code=1) from None
    try:
        result = extract_lecture(
            video_path,
            transcript_copy,
            output_dir,
            language=effective_language,
            visuals=config.visuals,
        )
    except Exception:
        typer.echo(
            "Local video processing failed. Check that ffmpeg and ffprobe are installed.", err=True
        )
        raise typer.Exit(code=1) from None
    try:
        safe_join(output_dir, "local.json").write_text(
            json.dumps(
                {
                    "video_filename": safe_component(video_path.name),
                    "transcript_filename": transcript_copy.name if transcript_copy else None,
                    "candidate_count": result.candidate_count,
                    "visual_count": result.visual_count,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not write local processing metadata: {exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"{result.candidate_count} visual candidates; {result.visual_count} final screenshots"
    )
    typer.echo(f"Output: {output_dir}")
