"""Bounded, read-only requests to the YouTube Data API v3."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_BASE_URL = "https://www.googleapis.com/youtube/v3"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_COMMENT_PAGES = 10


class YouTubeAPIError(Exception):
    """A sanitized error suitable for returning through our local API."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def extract_video_id(value: str) -> str:
    value = value.strip()
    if _VIDEO_ID.fullmatch(value):
        return value
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"https", "http"} or parts.username or parts.password:
            raise ValueError
        if parts.port is not None:
            raise ValueError
        host = (parts.hostname or "").lower()
        path = parts.path.rstrip("/")
        if host in {"youtu.be", "www.youtu.be"}:
            candidate = path.removeprefix("/")
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            if path == "/watch":
                values = parse_qs(parts.query).get("v", [])
                candidate = values[0] if len(values) == 1 else ""
            else:
                segments = path.strip("/").split("/")
                candidate = (
                    segments[1]
                    if len(segments) == 2 and segments[0] in {"shorts", "live", "embed"}
                    else ""
                )
        else:
            raise ValueError
        if not _VIDEO_ID.fullmatch(candidate):
            raise ValueError
        return candidate
    except ValueError:
        raise ValueError("Provide a valid YouTube video URL or 11-character video ID") from None


def _utc_naive(value: object) -> datetime | None:
    """Match the existing schema's naive DateTime columns, consistently in UTC."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise YouTubeAPIError(502, "YouTube returned an invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        raise YouTubeAPIError(502, "YouTube returned an invalid timestamp") from None


@dataclass(frozen=True)
class YouTubeVideo:
    video_id: str
    title: str
    channel_name: str | None
    published_at: datetime | None
    channel_id: str | None


@dataclass(frozen=True)
class YouTubeComment:
    platform_comment_id: str
    text: str
    like_count: int | None
    published_at: datetime | None
    updated_at: str | None


@dataclass(frozen=True)
class YouTubeCommentBatch:
    comments: list[YouTubeComment]
    fetched_count: int
    skipped_count: int
    pages_fetched: int
    next_page_token: str | None


@dataclass
class YouTubeClient:
    api_key: str = field(repr=False)
    timeout: float = 10.0

    def __post_init__(self):
        self.api_key = self.api_key.strip()
        if not self.api_key:
            raise YouTubeAPIError(503, "YOUTUBE_API_KEY is not configured on the backend")

    def _get(self, resource: str, parameters: dict) -> dict:
        # Only these fixed Google endpoints are allowed; video URLs aren't fetched.
        if resource not in {"videos", "commentThreads"}:
            raise ValueError("Unsupported YouTube resource")
        request = Request(
            f"{_BASE_URL}/{resource}?{urlencode(parameters)}",
            headers={"Accept": "application/json", "X-Goog-Api-Key": self.api_key},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise YouTubeAPIError(502, "YouTube returned an oversized response")
            body = json.loads(raw)
        except HTTPError as exc:
            # Never return the provider's body, request, headers or exception text:
            # upstream failures can contain credentials or identifying details.
            reason = None
            try:
                payload = json.loads(exc.read(65536))
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                reasons = error.get("errors", []) if isinstance(error, dict) else []
                if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict):
                    reason = reasons[0].get("reason")
            except (ValueError, AttributeError, TypeError, OSError):
                pass
            finally:
                exc.close()
            if reason == "commentsDisabled":
                raise YouTubeAPIError(422, "Comments are disabled for this video") from None
            if reason in {"videoNotFound", "notFound"} or exc.code == 404:
                raise YouTubeAPIError(404, "The video is unavailable or was not found") from None
            if reason == "invalidPageToken":
                raise YouTubeAPIError(422, "The page token is invalid; restart this video's collection") from None
            if reason in {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"} or exc.code == 429:
                raise YouTubeAPIError(503, "YouTube quota or rate limit reached; try again later") from None
            if exc.code in {400, 401, 403}:
                raise YouTubeAPIError(503, "YouTube rejected the request; check API activation, key restrictions and quota") from None
            raise YouTubeAPIError(502, "YouTube is temporarily unavailable") from None
        except (URLError, TimeoutError, OSError):
            raise YouTubeAPIError(502, "Could not reach YouTube; try again later") from None
        except (ValueError, UnicodeDecodeError):
            raise YouTubeAPIError(502, "YouTube returned an invalid JSON response") from None
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise YouTubeAPIError(502, "YouTube returned an unexpected response")
        return body

    def fetch_video(self, video_id: str) -> YouTubeVideo:
        body = self._get("videos", {"part": "snippet", "id": video_id})
        if not body["items"]:
            raise YouTubeAPIError(404, "The video is unavailable or was not found")
        try:
            video = body["items"][0]
            snippet = video["snippet"]
            title = snippet["title"]
            if video["id"] != video_id or not isinstance(title, str) or not title.strip():
                raise ValueError
            channel_name = snippet.get("channelTitle")
            channel_id = snippet.get("channelId")
            if any(value is not None and not isinstance(value, str)
                   for value in (channel_name, channel_id)):
                raise ValueError
            return YouTubeVideo(video_id, title, channel_name,
                                _utc_naive(snippet.get("publishedAt")), channel_id)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise YouTubeAPIError(502, "YouTube returned invalid video metadata") from None

    def fetch_comments(
        self, video_id: str, max_comments: int = 100, page_token: str | None = None,
    ) -> YouTubeCommentBatch:
        if not 1 <= max_comments <= 500:
            raise ValueError("max_comments must be between 1 and 500")
        comments = []
        fetched = skipped = pages = 0
        token = page_token
        seen_tokens = {token} if token else set()
        while fetched < max_comments and pages < _MAX_COMMENT_PAGES:
            requested = min(100, max_comments - fetched)
            parameters = {
                "part": "snippet", "videoId": video_id, "maxResults": requested,
                "order": "time", "textFormat": "plainText",
            }
            if token:
                parameters["pageToken"] = token
            body = self._get("commentThreads", parameters)
            pages += 1
            items = body["items"]
            if len(items) > requested:
                raise YouTubeAPIError(502, "YouTube returned too many comments for a page")
            for thread in items:
                try:
                    item = thread["snippet"]["topLevelComment"]
                    identifier = item["id"]
                    snippet = item["snippet"]
                    text = snippet["textDisplay"]
                    likes = snippet.get("likeCount")
                    updated = snippet.get("updatedAt")
                    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 255:
                        raise ValueError
                    if not isinstance(text, str):
                        raise ValueError
                    if likes is not None and (type(likes) is not int or not 0 <= likes <= 2147483647):
                        raise ValueError
                    if updated is not None and not isinstance(updated, str):
                        raise ValueError
                    published = _utc_naive(snippet.get("publishedAt"))
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise YouTubeAPIError(502, "YouTube returned invalid comment data") from None
                fetched += 1
                if not text.strip():
                    skipped += 1
                    continue
                comments.append(YouTubeComment(identifier, text, likes, published, updated))
            token = body.get("nextPageToken") or None
            if token is not None and (not isinstance(token, str) or len(token) > 2048):
                raise YouTubeAPIError(502, "YouTube returned an invalid page token")
            if not token:
                break
            if token in seen_tokens or not items:
                raise YouTubeAPIError(502, "YouTube pagination did not advance; retry collection")
            seen_tokens.add(token)
        return YouTubeCommentBatch(comments, fetched, skipped, pages, token)
