import json
from pathlib import Path

from coursera_notes.coursera.parser import parse_course_materials

FIXTURES = Path(__file__).parent / "fixtures"


def test_course_parser_preserves_order_and_skips_locked_lectures() -> None:
    payload = json.loads((FIXTURES / "course_materials.json").read_text())

    course = parse_course_materials(payload, requested_slug="machine-learning")

    assert course.id == "course-42"
    assert [module.name for module in course.modules] == ["Introduction", "Models"]
    assert [lecture.lecture_name for lecture in course.lectures] == [
        "Course Welcome",
        "Linear Regression",
    ]
    assert course.lectures[0].module_index == 1
    assert course.lectures[1].module_index == 2
    assert course.lectures[0].duration_seconds == 301


def test_course_parser_handles_inline_material_without_optional_fields() -> None:
    course = parse_course_materials(
        {
            "elements": [
                {
                    "id": "c1",
                    "name": "Course",
                    "slug": "course",
                    "modules": [
                        {
                            "id": "m1",
                            "name": "Week",
                            "lessons": [
                                {
                                    "id": "l1",
                                    "name": "Topic",
                                    "items": [
                                        {
                                            "id": "i1",
                                            "name": "Video",
                                            "contentSummary": {"typeName": "lecture"},
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        requested_slug="course",
    )

    assert course.lectures[0].lecture_id == "i1"
    assert course.lectures[0].duration_seconds is None


def test_empty_modules_and_lessons_remain_after_locked_lectures_are_skipped() -> None:
    course = parse_course_materials(
        {
            "elements": [
                {
                    "id": "c1",
                    "name": "Course",
                    "slug": "course",
                    "moduleIds": ["m1"],
                }
            ],
            "linked": {
                "onDemandCourseModules.v1": [
                    {"id": "m1", "name": "Restricted week", "lessonIds": ["l1"]}
                ],
                "onDemandCourseLessons.v1": [
                    {"id": "l1", "name": "Locked topic", "itemIds": ["i1"]}
                ],
                "onDemandCourseMaterialItems.v2": [
                    {
                        "id": "i1",
                        "name": "Preview",
                        "contentSummary": {"typeName": "lecture"},
                        "isLocked": True,
                    }
                ],
            },
        },
        requested_slug="course",
    )

    assert len(course.modules) == 1
    assert len(course.modules[0].lessons) == 1
    assert course.lectures == []


def test_course_parser_fallback_matches_specific_parent_among_ancestor_ids() -> None:
    course = parse_course_materials(
        {
            "elements": [{"id": "c1", "name": "Course", "slug": "course"}],
            "linked": {
                "onDemandCourseModules.v1": [{"id": "m1", "name": "Week", "courseId": "c1"}],
                "onDemandCourseLessons.v1": [
                    {"id": "l1", "name": "Topic", "courseId": "c1", "moduleId": "m1"}
                ],
                "onDemandCourseMaterialItems.v2": [
                    {
                        "id": "i1",
                        "name": "Lecture",
                        "courseId": "c1",
                        "moduleId": "m1",
                        "lessonId": "l1",
                        "contentSummary": {"typeName": "lecture"},
                    }
                ],
            },
        },
        requested_slug="course",
    )

    assert [lecture.lecture_id for lecture in course.lectures] == ["i1"]
