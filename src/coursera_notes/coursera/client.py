"""Authenticated Coursera API client for enrolled-course material."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from coursera_notes.coursera.models import LectureMetadata
from coursera_notes.coursera.parser import parse_course_materials, parse_lecture_metadata
from coursera_notes.models import Course


class CourseraAPIError(RuntimeError):
    """A sanitized Coursera API failure with no cookie or response body attached."""


def normalize_cauth(value: str) -> str:
    """Accept a raw token, a CAUTH assignment, or a complete Cookie header."""
    candidate = value.strip()
    if not candidate or "\r" in candidate or "\n" in candidate:
        raise ValueError("A valid CAUTH token or Cookie header is required")
    if candidate.casefold().startswith("cookie:"):
        candidate = candidate.split(":", 1)[1].strip()
    parts = [part.strip() for part in candidate.split(";") if part.strip()]
    cauth = next(
        (part for part in parts if part.split("=", 1)[0].strip().casefold() == "cauth"), None
    )
    if cauth is not None:
        _, token = cauth.split("=", 1)
        if not token.strip():
            raise ValueError("A valid CAUTH token or Cookie header is required")
        return "; ".join(parts)
    if len(parts) > 1 or ";" in candidate:
        raise ValueError("Cookie header must include a CAUTH cookie")
    raw_token = parts[0] if parts else candidate
    if raw_token.casefold().startswith("cauth="):
        raw_token = raw_token.split("=", 1)[1].strip()
    if not raw_token:
        raise ValueError("A valid CAUTH token or Cookie header is required")
    return f"CAUTH={raw_token}"


class CourseraClient:
    API_BASE = "https://www.coursera.org"

    def __init__(self, cauth: str, *, transport: httpx.BaseTransport | None = None) -> None:
        cookie = normalize_cauth(cauth)
        timeout = httpx.Timeout(connect=15, read=45, write=30, pool=15)
        self._client = httpx.Client(
            headers={"Cookie": cookie, "User-Agent": "coursera-notes/0.1"},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def get_course(self, slug: str) -> Course:
        if not slug.strip():
            raise ValueError("Course slug cannot be empty")
        payload = self._get_json(
            "/api/onDemandCourseMaterials.v2/",
            params={"q": "slug", "slug": slug},
        )
        try:
            return parse_course_materials(payload, requested_slug=slug)
        except (TypeError, ValueError) as exc:
            raise CourseraAPIError("Coursera returned an unrecognized course structure") from exc

    def get_lecture_metadata(self, course_id: str, item_id: str) -> LectureMetadata:
        resource_id = quote(f"{course_id}~{item_id}", safe="~")
        payload = self._get_json(
            f"/api/onDemandLectureVideos.v1/{resource_id}",
            params={"includes": "video"},
        )
        try:
            return parse_lecture_metadata(payload)
        except (TypeError, ValueError) as exc:
            raise CourseraAPIError("Coursera returned unrecognized lecture metadata") from exc

    def _get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        try:
            response = self._client.get(f"{self.API_BASE}{path}", params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise CourseraAPIError(
                f"Coursera API returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError:
            raise CourseraAPIError("Coursera API request failed") from None
        try:
            payload = response.json()
        except ValueError:
            raise CourseraAPIError("Coursera API returned invalid JSON") from None
        if not isinstance(payload, Mapping):
            raise CourseraAPIError("Coursera API returned an unexpected response")
        return payload

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CourseraClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
