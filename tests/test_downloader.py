from pathlib import Path

import httpx
import pytest

from coursera_notes.coursera.downloader import DownloadError, download_file


def test_download_streams_response_to_destination_without_auth_headers(tmp_path: Path) -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"video-data"))

    target = tmp_path / "video.mp4"
    with httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Cookie": "CAUTH=must-not-leak"}
    ) as client:
        result = download_file(
            "https://cdn.example/video.mp4?sig=private", target, client=client, retries=0
        )

    assert target.read_bytes() == b"video-data"
    assert result.skipped is False
    assert "cookie" not in observed[0].headers


def test_download_resumes_partial_file_with_range_request(tmp_path: Path) -> None:
    target = tmp_path / "video.mp4"
    part = target.with_name("video.mp4.part")
    part.write_bytes(b"ab")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == "bytes=2-"
        return httpx.Response(
            206,
            headers={"Content-Range": "bytes 2-4/5", "Content-Length": "3"},
            stream=httpx.ByteStream(b"cde"),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_file("https://cdn.example/video.mp4", target, client=client, retries=0)

    assert target.read_bytes() == b"abcde"
    assert result.bytes_written == 3
    assert not part.exists()


def test_server_ignoring_range_restarts_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "video.mp4"
    target.with_name("video.mp4.part").write_bytes(b"old-part")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == "bytes=8-"
        return httpx.Response(
            200,
            headers={"Content-Length": "10"},
            stream=httpx.ByteStream(b"fresh-file"),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        download_file("https://cdn.example/video.mp4", target, client=client, retries=0)

    assert target.read_bytes() == b"fresh-file"


def test_existing_complete_file_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "transcript.txt"
    target.write_text("already fetched")

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("an existing file must not trigger a request")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_file("https://cdn.example/transcript.txt", target, client=client)

    assert result.skipped is True
    assert target.read_text() == "already fetched"


def test_temporary_server_error_retries_without_exposing_the_url(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={"Content-Length": "9"},
            stream=httpx.ByteStream(b"recovered"),
        )

    target = tmp_path / "video.mp4"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_file(
            "https://cdn.example/video.mp4?signature=secret",
            target,
            client=client,
            retries=1,
            backoff_seconds=0,
        )

    assert result.path.read_bytes() == b"recovered"
    assert calls == 2


def test_terminal_error_does_not_include_signed_url(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError) as error:
            download_file(
                "https://cdn.example/video.mp4?signature=private",
                tmp_path / "video.mp4",
                client=client,
                retries=0,
            )

    assert "signature=private" not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_https_media_redirect_cannot_downgrade_to_http(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "http://plain.example/video.mp4"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DownloadError, match="non-HTTPS"):
            download_file(
                "https://cdn.example/video.mp4?signature=private",
                tmp_path / "video.mp4",
                client=client,
                retries=0,
            )

    assert len(requests) == 1
    assert requests[0].url.scheme == "https"


def test_download_rejects_symlinked_partial_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"protected")
    (tmp_path / "video.mp4.part").symlink_to(outside)

    with pytest.raises(DownloadError, match="symbolic link"):
        download_file("https://cdn.example/video.mp4", tmp_path / "video.mp4", retries=0)

    assert outside.read_bytes() == b"protected"
