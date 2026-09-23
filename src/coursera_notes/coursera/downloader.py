"""Streaming, resumable HTTP downloads for videos and official subtitles."""

from __future__ import annotations

import os
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict


class DownloadResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    path: Path
    skipped: bool = False
    bytes_written: int


class DownloadError(RuntimeError):
    """A download failed; the exception deliberately omits the signed media URL."""


class _NonRetryableDownloadError(DownloadError):
    pass


_TIMEOUT = httpx.Timeout(connect=20, read=90, write=30, pool=20)
_CHUNK_BYTES = 1024 * 1024


def download_file(
    url: str,
    destination: Path,
    *,
    overwrite: bool = False,
    retries: int = 3,
    backoff_seconds: float = 0.5,
    client: httpx.Client | None = None,
) -> DownloadResult:
    """Stream a URL to disk, resuming a partial file when the server supports Range."""
    if retries < 0 or backoff_seconds < 0:
        raise ValueError("retry and backoff values cannot be negative")
    try:
        initial_url = httpx.URL(url)
    except (TypeError, ValueError):
        raise DownloadError("Only valid HTTPS media URLs are accepted") from None
    if initial_url.scheme != "https" or not initial_url.host:
        raise DownloadError("Only HTTPS media URLs are accepted")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    if destination.is_symlink() or partial.is_symlink():
        raise DownloadError("Download target must not be a symbolic link")
    if destination.is_file() and destination.stat().st_size > 0 and not overwrite:
        return DownloadResult(path=destination, skipped=True, bytes_written=0)
    if overwrite:
        destination.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    elif destination.exists():
        destination.unlink(missing_ok=True)

    own_client = client is None
    http = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    force_restart = False
    try:
        for attempt in range(retries + 1):
            resume_size = partial.stat().st_size if partial.is_file() and not force_restart else 0
            headers = {"Accept-Encoding": "identity"}
            if resume_size:
                headers["Range"] = f"bytes={resume_size}-"
            response: httpx.Response | None = None
            try:
                response = _send_https_redirects(http, url, headers)
                if response.status_code == 416 and resume_size:
                    total = _range_total(response.headers.get("content-range"))
                    response.close()
                    if total is not None and total == resume_size:
                        os.replace(partial, destination)
                        return DownloadResult(path=destination, bytes_written=0)
                    partial.unlink(missing_ok=True)
                    force_restart = True
                    if attempt < retries:
                        continue
                    raise DownloadError("The media server rejected the resume request")
                if response.status_code >= 300:
                    status = response.status_code
                    response.close()
                    response = None
                    if status not in (408, 429) and status < 500:
                        raise _NonRetryableDownloadError(f"Media server returned HTTP {status}")
                    raise DownloadError(f"Media server returned HTTP {status}")

                append = resume_size > 0 and response.status_code == 206
                if append:
                    range_start = _range_start(response.headers.get("content-range"))
                    if range_start != resume_size:
                        response.close()
                        response = None
                        partial.unlink(missing_ok=True)
                        force_restart = True
                        if attempt < retries:
                            continue
                        raise DownloadError("The media server returned an invalid range")
                elif response.status_code == 206 and not resume_size:
                    response.close()
                    response = None
                    raise DownloadError("The media server returned an unexpected partial response")

                mode = "ab" if append else "wb"
                if not append:
                    force_restart = False
                expected_body = _content_length(response.headers.get("content-length"))
                written = 0
                with partial.open(mode) as output:
                    for chunk in response.iter_raw(chunk_size=_CHUNK_BYTES):
                        if chunk:
                            output.write(chunk)
                            written += len(chunk)
                expected_total = (
                    _range_total(response.headers.get("content-range")) if append else None
                )
                response.close()
                response = None
                if expected_body is not None and written != expected_body:
                    raise DownloadError("The media response ended before all bytes were received")
                if expected_total is not None and partial.stat().st_size != expected_total:
                    raise DownloadError("The resumed media response is incomplete")
                os.replace(partial, destination)
                return DownloadResult(path=destination, bytes_written=written)
            except _NonRetryableDownloadError:
                raise
            except DownloadError:
                pass
            except (httpx.TransportError, OSError):
                pass
            finally:
                if response is not None:
                    response.close()
            if attempt < retries:
                time.sleep(backoff_seconds * (2**attempt) + random.uniform(0, backoff_seconds / 2))
        raise DownloadError("Media download failed after retries") from None
    finally:
        if own_client:
            http.close()


def _send_https_redirects(
    client: httpx.Client, url: str, headers: dict[str, str]
) -> httpx.Response:
    """Follow media redirects manually so an HTTPS URL cannot downgrade to HTTP."""
    current_url = url
    for _ in range(10):
        request = client.build_request("GET", current_url, headers=headers, timeout=_TIMEOUT)
        # Media requests must never receive Coursera session credentials, even if
        # the supplied client carries them as default headers.
        request.headers.pop("cookie", None)
        request.headers.pop("authorization", None)
        response = client.send(request, stream=True, follow_redirects=False)
        if response.status_code not in {301, 302, 303, 307, 308}:
            if response.url.scheme != "https":
                response.close()
                raise _NonRetryableDownloadError("Media server used a non-HTTPS URL")
            return response

        location = response.headers.get("location")
        if not location:
            return response
        try:
            next_url = httpx.URL(urljoin(str(response.url), location))
        except (TypeError, ValueError):
            response.close()
            raise _NonRetryableDownloadError("Media server returned an invalid redirect") from None
        response.close()
        if next_url.scheme != "https" or not next_url.host:
            raise _NonRetryableDownloadError("Media server redirected to a non-HTTPS URL")
        current_url = str(next_url)
    raise _NonRetryableDownloadError("Media server exceeded the redirect limit")


def _content_length(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _range_start(value: str | None) -> int | None:
    match = re.match(r"bytes\s+(\d+)-", value or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _range_total(value: str | None) -> int | None:
    match = re.search(r"/(\d+)\s*$", value or "", re.IGNORECASE)
    return int(match.group(1)) if match else None
