from pathlib import Path

import pytest

from coursera_notes.models import Lecture
from coursera_notes.output.paths import lecture_relative_dir, safe_component, safe_join


def test_safe_join_prevents_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..", "outside.txt")


def test_safe_join_rejects_symlink_components(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "output"
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic links"):
        safe_join(root, "linked", "asset.mp4")


def test_lecture_path_contains_stable_order_and_sanitized_slugs() -> None:
    lecture = Lecture(
        course_id="c1",
        module_id="m1",
        module_index=2,
        module_name="Week 2",
        module_slug="week-two",
        lesson_id="l1",
        lesson_slug="lesson-three",
        lesson_index=3,
        lecture_id="i1",
        lecture_index=4,
        lecture_name="A / B",
        lecture_slug="a-b",
    )

    assert lecture_relative_dir(lecture).as_posix() == "02_week-two/03_lesson-three/04_a-b"
    assert safe_component("../week/one") == "week-one"
