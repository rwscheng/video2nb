import json
from pathlib import Path

import httpx

from coursera_notes.coursera.client import CourseraClient, normalize_cauth

FIXTURES = Path(__file__).parent / "fixtures"


def test_cauth_accepts_token_prefix_and_cookie_header() -> None:
    assert normalize_cauth(" raw-token ") == "CAUTH=raw-token"
    assert normalize_cauth("CAUTH=raw-token") == "CAUTH=raw-token"
    assert normalize_cauth("Cookie: CAUTH=raw-token; other=value") == "CAUTH=raw-token; other=value"


def test_course_and_lecture_metadata_use_expected_api_routes() -> None:
    course_payload = json.loads((FIXTURES / "course_materials.json").read_text())
    lecture_payload = json.loads((FIXTURES / "lecture_video.json").read_text())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("onDemandCourseMaterials.v2/"):
            return httpx.Response(200, json=course_payload)
        if request.url.path.endswith("onDemandLectureVideos.v1/course-42~lecture-first"):
            return httpx.Response(200, json=lecture_payload)
        return httpx.Response(404)

    with CourseraClient("CAUTH=private-token", transport=httpx.MockTransport(handler)) as client:
        course = client.get_course("machine-learning")
        metadata = client.get_lecture_metadata("course-42", "lecture-first")
        client_text = repr(client)

    assert course.name == "Machine Learning"
    assert metadata.video_sources and metadata.transcript_sources
    assert len(requests) == 2
    assert requests[0].url.params["q"] == "slug"
    assert requests[0].url.params["slug"] == "machine-learning"
    assert requests[1].url.params["includes"] == "video"
    assert all(request.headers.get("cookie") == "CAUTH=private-token" for request in requests)
    assert "private-token" not in client_text
