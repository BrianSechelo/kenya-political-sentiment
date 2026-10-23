import io
import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from app.schemas.youtube import YouTubeIngestRequest
from app.services import youtube_client as module
from app.services.youtube_client import YouTubeAPIError, YouTubeClient, extract_video_id


VIDEO_ID = "AbC123_-xyz"


def thread(identifier="comment-1", text="William Ruto did an excellent job"):
    return {"snippet": {"videoId": VIDEO_ID, "topLevelComment": {
        "id": identifier,
        "snippet": {
            "textDisplay": text, "likeCount": 3,
            "publishedAt": "2026-10-20T10:30:00Z",
            "updatedAt": "2026-10-20T10:30:00Z",
            "authorDisplayName": "Not stored",
            "authorChannelId": {"value": "not-stored-either"},
        },
    }}}


@pytest.mark.parametrize("value", [
    VIDEO_ID,
    f" https://www.youtube.com/watch?v={VIDEO_ID}&t=30 ",
    f"https://youtube.com/watch?v={VIDEO_ID}",
    f"https://m.youtube.com/watch?v={VIDEO_ID}",
    f"https://music.youtube.com/watch?v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}?si=example",
    f"https://www.youtube.com/shorts/{VIDEO_ID}",
    f"https://www.youtube.com/live/{VIDEO_ID}",
    f"https://www.youtube.com/embed/{VIDEO_ID}/",
])
def test_extract_supported_video_identifiers(value):
    assert extract_video_id(value) == VIDEO_ID


@pytest.mark.parametrize("value", [
    "", "short", "abcdefghijk/extra", "youtube.com/watch?v=" + VIDEO_ID,
    f"https://youtube.com.evil.test/watch?v={VIDEO_ID}",
    f"https://evil.test/?v={VIDEO_ID}", f"file:///watch?v={VIDEO_ID}",
    f"https://user@youtube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com:8080/watch?v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}/extra", "https://youtube.com/watch?v=bad",
    f"https://youtube.com/watch?v={VIDEO_ID}&v={VIDEO_ID}",
])
def test_reject_invalid_or_non_youtube_urls(value):
    with pytest.raises(ValueError, match="valid YouTube"):
        extract_video_id(value)


@pytest.mark.parametrize("count", [0, 501, True, "100"])
def test_request_limits_are_validated(count):
    with pytest.raises(ValidationError):
        YouTubeIngestRequest(video_url=VIDEO_ID, max_comments=count)


def test_request_rejects_extra_api_key():
    with pytest.raises(ValidationError):
        YouTubeIngestRequest(video_url=VIDEO_ID, api_key="never-send-a-key-in-the-body")


def test_http_uses_fixed_endpoint_and_header_key(monkeypatch):
    seen = []

    def fake_open(request, timeout):
        seen.append((request, timeout))
        return io.BytesIO(b'{"items": []}')

    monkeypatch.setattr(module, "urlopen", fake_open)
    client = YouTubeClient("secret-test-key")
    client._get("videos", {"part": "snippet", "id": VIDEO_ID})
    request, timeout = seen[0]
    assert urlsplit(request.full_url).hostname == "www.googleapis.com"
    assert "secret-test-key" not in request.full_url
    assert {key.lower(): value for key, value in request.header_items()}["x-goog-api-key"] == "secret-test-key"
    assert timeout == 10
    assert "secret-test-key" not in repr(client)


def test_missing_key_is_clear():
    with pytest.raises(YouTubeAPIError) as caught:
        YouTubeClient("  ")
    assert caught.value.status_code == 503


@pytest.mark.parametrize("status,reason,expected", [
    (403, "commentsDisabled", 422), (404, "videoNotFound", 404),
    (403, "quotaExceeded", 503), (403, "dailyLimitExceeded", 503),
    (429, "rateLimitExceeded", 503), (400, "keyInvalid", 503),
    (403, "accessNotConfigured", 503), (400, "invalidPageToken", 422),
    (500, "backendError", 502),
])
def test_upstream_errors_are_sanitized(monkeypatch, status, reason, expected):
    def fake_open(request, timeout):
        body = json.dumps({"error": {"message": "secret-test-key",
                                    "errors": [{"reason": reason}]}}).encode()
        raise HTTPError(request.full_url, status, "secret-test-key", {}, io.BytesIO(body))

    monkeypatch.setattr(module, "urlopen", fake_open)
    with pytest.raises(YouTubeAPIError) as caught:
        YouTubeClient("secret-test-key")._get("videos", {"id": VIDEO_ID})
    assert caught.value.status_code == expected
    assert "secret-test-key" not in str(caught.value)


@pytest.mark.parametrize("failure", [URLError("secret-test-key"), TimeoutError("secret-test-key")])
def test_network_failures_are_sanitized(monkeypatch, failure):
    def fake_open(request, timeout):
        raise failure

    monkeypatch.setattr(module, "urlopen", fake_open)
    with pytest.raises(YouTubeAPIError) as caught:
        YouTubeClient("secret-test-key")._get("videos", {})
    assert caught.value.status_code == 502
    assert "secret-test-key" not in str(caught.value)


@pytest.mark.parametrize("error_body", [
    b"not-json", b"[]", b'{"error": []}', b'{"error": {"errors": {"unexpected": "secret-test-key"}}}',
])
def test_malformed_error_body_still_has_sanitized_error(monkeypatch, error_body):
    def fake_open(request, timeout):
        raise HTTPError(request.full_url, 403, "secret-test-key", {}, io.BytesIO(error_body))

    monkeypatch.setattr(module, "urlopen", fake_open)
    with pytest.raises(YouTubeAPIError) as caught:
        YouTubeClient("secret-test-key")._get("videos", {})
    assert caught.value.status_code == 503
    assert "secret-test-key" not in str(caught.value)


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"items": {}}'])
def test_malformed_responses_are_rejected(monkeypatch, raw):
    monkeypatch.setattr(module, "urlopen", lambda request, timeout: io.BytesIO(raw))
    with pytest.raises(YouTubeAPIError) as caught:
        YouTubeClient("test-key")._get("videos", {})
    assert caught.value.status_code == 502


def test_pagination_respects_remaining_limit_and_preserves_resume_token(monkeypatch):
    calls = []

    def fake_get(resource, params):
        calls.append((resource, params.copy()))
        if len(calls) == 1:
            return {"items": [thread(f"first-{i}") for i in range(100)], "nextPageToken": "page-2"}
        return {"items": [thread(f"second-{i}") for i in range(25)], "nextPageToken": "page-3"}

    client = YouTubeClient("test-key")
    monkeypatch.setattr(client, "_get", fake_get)
    result = client.fetch_comments(VIDEO_ID, 125)
    assert result.fetched_count == len(result.comments) == 125
    assert result.pages_fetched == 2
    assert result.next_page_token == "page-3"
    assert calls[1][1]["maxResults"] == 25
    assert calls[1][1]["pageToken"] == "page-2"
    assert calls[0][1]["order"] == "time"
    assert calls[0][1]["textFormat"] == "plainText"
    assert result.comments[0].published_at == datetime(2026, 10, 20, 10, 30)


def test_resumed_batch_uses_given_token(monkeypatch):
    client = YouTubeClient("test-key")
    seen = []
    monkeypatch.setattr(client, "_get", lambda resource, params: seen.append(params) or {"items": []})
    result = client.fetch_comments(VIDEO_ID, 1, "resume-token")
    assert seen[0]["pageToken"] == "resume-token"
    assert result.next_page_token is None


def test_blank_comments_count_as_skipped(monkeypatch):
    client = YouTubeClient("test-key")
    monkeypatch.setattr(client, "_get", lambda *_: {"items": [thread(text="   ")]})
    result = client.fetch_comments(VIDEO_ID)
    assert result.fetched_count == result.skipped_count == 1
    assert result.comments == []


def test_repeated_page_token_is_rejected(monkeypatch):
    client = YouTubeClient("test-key")
    monkeypatch.setattr(client, "_get", lambda *_: {"items": [thread()], "nextPageToken": "same"})
    with pytest.raises(YouTubeAPIError, match="pagination"):
        client.fetch_comments(VIDEO_ID, 3)


def test_page_budget_returns_resume_token(monkeypatch):
    client = YouTubeClient("test-key")
    calls = []

    def fake_get(*_):
        calls.append(1)
        return {"items": [thread(str(len(calls)))], "nextPageToken": f"page-{len(calls)}"}

    monkeypatch.setattr(client, "_get", fake_get)
    result = client.fetch_comments(VIDEO_ID, 500)
    assert len(calls) == result.pages_fetched == 10
    assert result.next_page_token == "page-10"


def test_unavailable_video_is_reported(monkeypatch):
    client = YouTubeClient("test-key")
    monkeypatch.setattr(client, "_get", lambda *_: {"items": []})
    with pytest.raises(YouTubeAPIError) as caught:
        client.fetch_video(VIDEO_ID)
    assert caught.value.status_code == 404


@pytest.mark.parametrize("bad_value", ["bad-date", "2026-10-20T10:00:00", 123])
def test_invalid_upstream_timestamp_rejected(monkeypatch, bad_value):
    item = thread()
    item["snippet"]["topLevelComment"]["snippet"]["publishedAt"] = bad_value
    client = YouTubeClient("test-key")
    monkeypatch.setattr(client, "_get", lambda *_: {"items": [item]})
    with pytest.raises(YouTubeAPIError):
        client.fetch_comments(VIDEO_ID)