import json
from pathlib import Path

from PIL import Image

from coursera_notes.models import Course
from coursera_notes.output.manifest import build_notebooklm_manifest
from coursera_notes.output.notebooklm import package_notebooklm
from coursera_notes.output.paths import safe_component


def test_safe_component_blocks_traversal_and_normalizes_names() -> None:
    assert safe_component("../week/one") == "week-one"
    assert safe_component("..") == "untitled"


def test_notebooklm_manifest_links_transcript_and_visual_paths() -> None:
    course = Course.model_validate(
        {
            "id": "c1",
            "name": "Machine Learning",
            "slug": "machine-learning",
            "modules": [
                {
                    "id": "m1",
                    "name": "Introduction",
                    "slug": "intro",
                    "index": 1,
                    "lessons": [
                        {
                            "id": "l1",
                            "name": "Welcome",
                            "slug": "welcome",
                            "index": 1,
                            "lectures": [
                                {
                                    "course_id": "c1",
                                    "module_id": "m1",
                                    "module_index": 1,
                                    "module_name": "Introduction",
                                    "module_slug": "intro",
                                    "lesson_id": "l1",
                                    "lesson_index": 1,
                                    "lecture_id": "i1",
                                    "lecture_index": 1,
                                    "lecture_name": "Welcome",
                                    "lecture_slug": "welcome",
                                    "duration_seconds": 60,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    visuals = {
        "i1": [
            {
                "timestamp_seconds": 15,
                "timestamp": "00:00:15",
                "filename": "00-00-15.jpg",
                "sources": ["scene_change"],
            }
        ]
    }

    manifest = build_notebooklm_manifest(course, visuals)

    assert "transcript/01_01_01_welcome.txt" in manifest
    assert "visuals/01_01_01_welcome/00-00-15.jpg" in manifest


def test_package_converts_subtitles_and_copies_only_selected_assets(tmp_path: Path) -> None:
    course = Course.model_validate(
        {
            "id": "c1",
            "name": "Machine Learning",
            "slug": "machine-learning",
            "modules": [
                {
                    "id": "m1",
                    "name": "Introduction",
                    "slug": "intro",
                    "index": 1,
                    "lessons": [
                        {
                            "id": "l1",
                            "name": "Welcome",
                            "slug": "welcome",
                            "index": 1,
                            "lectures": [
                                {
                                    "course_id": "c1",
                                    "module_id": "m1",
                                    "module_index": 1,
                                    "module_name": "Introduction",
                                    "module_slug": "intro",
                                    "lesson_id": "l1",
                                    "lesson_slug": "welcome",
                                    "lesson_index": 1,
                                    "lecture_id": "i1",
                                    "lecture_index": 1,
                                    "lecture_name": "Welcome",
                                    "lecture_slug": "welcome",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    lecture_dir = tmp_path / "01_intro/01_welcome/01_welcome"
    lecture_dir.mkdir(parents=True)
    (lecture_dir / "subtitles.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nWelcome to class.\n"
    )
    (lecture_dir / "visual_notes.md").write_text("# Lecture Visual Context\n")
    (lecture_dir / "video.mp4").write_bytes(b"original video")
    visual_dir = lecture_dir / "visuals"
    visual_dir.mkdir()
    Image.new("RGB", (32, 32), "navy").save(visual_dir / "00-00-01.jpg")
    (lecture_dir / "visual_manifest.json").write_text(
        json.dumps(
            [
                {
                    "timestamp_seconds": 1,
                    "timestamp": "00:00:01",
                    "filename": "00-00-01.jpg",
                    "sources": ["scene_change"],
                }
            ]
        )
    )

    package = package_notebooklm(tmp_path, course)

    transcript = (package / "transcript/01_01_01_welcome.txt").read_text()
    assert transcript == "Welcome to class.\n"
    assert (package / "visuals/01_01_01_welcome/00-00-01.jpg").is_file()
    assert (package / "visuals/01_01_01_welcome/visual_notes.md").is_file()
    assert not any(package.rglob("*.mp4"))
    assert "Transcript: `transcript/01_01_01_welcome.txt`" in (package / "manifest.md").read_text()

    user_file = package / "user-note.md"
    user_file.write_text("Keep this local note.")
    (lecture_dir / "subtitles.srt").unlink()
    (lecture_dir / "visual_notes.md").unlink()
    (lecture_dir / "visual_manifest.json").write_text("[]")

    package_notebooklm(tmp_path, course)

    assert not (package / "transcript/01_01_01_welcome.txt").exists()
    assert not (package / "visuals/01_01_01_welcome/00-00-01.jpg").exists()
    assert not (package / "visuals/01_01_01_welcome/visual_notes.md").exists()
    assert user_file.read_text() == "Keep this local note."
