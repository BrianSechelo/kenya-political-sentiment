"""Exercise the real API/ORM/mention/sentiment pipeline with mocked Google HTTP.

Each test gets a private SQLite database. PostgreSQL JSONB is replaced with JSON
only in the test DDL. No Google API key or network access is required.
"""

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, MetaData, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Comment, Mention, Politician, PoliticianAlias, SentimentAnalysis, Source
from app.routers import youtube as youtube_router
from app.services import youtube_client, youtube_ingestion
from app.services.youtube_client import YouTubeClient


VIDEO_ID = "AbC123_-xyz"


def comment_thread(identifier="yt-comment-1", text="William Ruto has done an excellent job"):
    return {"snippet": {"videoId": VIDEO_ID, "topLevelComment": {
        "id": identifier,
        "snippet": {
            "textDisplay": text, "likeCount": 2,
            "publishedAt": "2026-10-20T13:30:00+03:00",
            "authorDisplayName": "Must not be saved",
            "authorChannelId": {"value": "author-id-must-not-be-saved"},
        },
    }}}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for original in Base.metadata.sorted_tables:
        table = original.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
    metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        session.add_all([
            Politician(politician_id=1, full_name="William Ruto", slug="william-ruto"),
            Politician(politician_id=8, full_name="Raila Odinga", slug="raila-odinga"),
        ])
        session.flush()
        session.add(PoliticianAlias(politician_id=1, alias="Ruto", alias_type="surname"))
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def api(db, monkeypatch):
    def unexpected_network(*args, **kwargs):
        raise AssertionError("Test must stub all YouTube HTTP calls")

    monkeypatch.setattr(youtube_client, "urlopen", unexpected_network)
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[youtube_router.get_youtube_client] = lambda: YouTubeClient("test-key")
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture
def google(monkeypatch):
    state = {"items": [comment_thread()], "calls": [], "title": "Political discussion"}

    def fake_open(request, timeout):
        parts = urlsplit(request.full_url)
        params = parse_qs(parts.query)
        state["calls"].append((parts.path, params))
        if parts.path.endswith("/videos"):
            body = {"items": [{"id": VIDEO_ID, "snippet": {
                "title": state["title"], "channelTitle": "Example channel",
                "channelId": "publisher-channel", "publishedAt": "2026-10-19T08:00:00Z",
            }}]}
        else:
            assert parts.path.endswith("/commentThreads")
            if "failure" in state:
                status, reason = state["failure"]
                payload = json.dumps({"error": {"errors": [{"reason": reason}],
                                               "message": "test-key"}}).encode()
                raise HTTPError(request.full_url, status, "test-key", {}, io.BytesIO(payload))
            body = {"items": state["items"]}
            if state.get("next_token"):
                body["nextPageToken"] = state["next_token"]
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(youtube_client, "urlopen", fake_open)
    return state


def test_collection_through_sentiment_and_duplicate_prevention(api, db, google):
    google["items"].append(comment_thread("unmatched", "A discussion about transport"))
    response = api.post("/youtube/ingest", json={"video_url": VIDEO_ID})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["created_count"] == data["fetched_count"] == 2
    assert data["mentions_created"] == 1
    assert data["source_created"] is True
    assert data["comment_scope"] == "top_level"
    assert data["has_more"] is False
    assert db.query(Source).one().source_url.endswith(VIDEO_ID)
    saved = db.query(Comment).filter_by(platform_comment_id="yt-comment-1").one()
    assert saved.published_at.hour == 10  # Naive UTC for the existing schema.
    assert saved.author_hash is None
    assert "Must not be saved" not in json.dumps(saved.raw_data)
    assert "author-id-must-not-be-saved" not in json.dumps(saved.raw_data)

    analyzed = api.post("/sentiment-analyses/analyze-pending").json()
    assert analyzed["processed_count"] == 1
    assert analyzed["analyses"][0]["sentiment_label"] == "positive"
    assert api.post("/sentiment-analyses/analyze-pending").json()["processed_count"] == 0

    repeat = api.post("/youtube/ingest", json={"video_url": VIDEO_ID}).json()
    assert repeat["created_count"] == repeat["mentions_created"] == 0
    assert repeat["existing_count"] == 2
    assert repeat["source_created"] is False
    assert db.query(Source).count() == 1
    assert db.query(Comment).count() == 2
    assert db.query(Mention).count() == 1
    assert db.query(SentimentAnalysis).count() == 1
    assert api.get("/sentiment-analyses/summary").json()["total_analyses"] == 1


def test_registered_alias_matches_without_full_name(api, db, google):
    google["items"] = [comment_thread(text="Ruto spoke today")]
    result = api.post("/youtube/ingest", json={"video_url": VIDEO_ID}).json()
    assert result["mentions_created"] == 1


def test_duplicate_items_within_batch_create_one_row(api, db, google):
    google["items"] = [comment_thread(), comment_thread()]
    result = api.post("/youtube/ingest", json={"video_url": VIDEO_ID}).json()
    assert result["created_count"] == result["existing_count"] == 1
    assert db.query(Comment).count() == db.query(Mention).count() == 1


def test_empty_video_saves_source_and_reports_zero(api, db, google):
    google["items"] = []
    result = api.post("/youtube/ingest", json={"video_url": VIDEO_ID}).json()
    assert result["fetched_count"] == result["created_count"] == 0
    assert db.query(Source).count() == 1
    assert db.query(Comment).count() == 0


def test_blank_comment_is_counted_without_a_row(api, db, google):
    google["items"] = [comment_thread(text="   ")]
    result = api.post("/youtube/ingest", json={"video_url": VIDEO_ID}).json()
    assert result["fetched_count"] == result["skipped_count"] == 1
    assert result["created_count"] == 0
    assert db.query(Comment).count() == 0


def test_resume_token_in_response_and_next_request(api, db, google):
    google["next_token"] = "next-page"
    first = api.post("/youtube/ingest", json={"video_url": VIDEO_ID, "max_comments": 1}).json()
    assert first["has_more"] is True
    assert first["next_page_token"] == "next-page"
    google["items"] = [comment_thread("second-page-comment")]
    google.pop("next_token")
    second = api.post("/youtube/ingest", json={
        "video_url": VIDEO_ID, "max_comments": 1, "page_token": first["next_page_token"],
    }).json()
    assert second["created_count"] == 1
    assert google["calls"][-1][1]["pageToken"] == ["next-page"]
    assert db.query(Comment).count() == 2


def test_existing_comments_are_not_rewritten_after_sentiment(api, db, google):
    api.post("/youtube/ingest", json={"video_url": VIDEO_ID})
    api.post("/sentiment-analyses/analyze-pending")
    google["items"] = [comment_thread(text="Now an edited, terrible comment")]
    google["title"] = "Updated video title"
    repeat = api.post("/youtube/ingest", json={"video_url": VIDEO_ID}).json()
    assert repeat["existing_count"] == 1
    assert "excellent" in db.query(Comment).one().text
    assert db.query(SentimentAnalysis).one().sentiment_label == "positive"
    assert db.query(Source).one().title == "Updated video title"


@pytest.mark.parametrize("failure,status", [((403, "commentsDisabled"), 422),
                                          ((403, "quotaExceeded"), 503),
                                          ((400, "keyInvalid"), 503)])
def test_upstream_failure_leaves_no_partial_batch(api, db, google, failure, status):
    google["failure"] = failure
    response = api.post("/youtube/ingest", json={"video_url": VIDEO_ID})
    assert response.status_code == status
    assert "test-key" not in response.text
    assert db.query(Source).count() == db.query(Comment).count() == 0


def test_failure_on_later_page_does_not_save_first_page(api, db, monkeypatch):
    calls = []

    def fake_get(self, resource, params):
        if resource == "videos":
            return {"items": [{"id": VIDEO_ID, "snippet": {"title": "Test video"}}]}
        calls.append(params)
        if len(calls) == 1:
            return {"items": [comment_thread()], "nextPageToken": "second-page"}
        raise youtube_client.YouTubeAPIError(502, "Simulated later-page failure")

    monkeypatch.setattr(YouTubeClient, "_get", fake_get)
    response = api.post("/youtube/ingest", json={"video_url": VIDEO_ID, "max_comments": 2})
    assert response.status_code == 502
    assert len(calls) == 2
    assert db.query(Source).count() == db.query(Comment).count() == 0


def test_mention_failure_rolls_back_source_comments_and_mentions(api, db, google, monkeypatch):
    def fail_after_staging(session, comment):
        session.add(Mention(comment_id=comment.comment_id, politician_id=1))
        session.flush()
        raise RuntimeError("Simulated detection failure")

    monkeypatch.setattr(youtube_ingestion, "create_mentions_for_comment", fail_after_staging)
    response = api.post("/youtube/ingest", json={"video_url": VIDEO_ID})
    assert response.status_code == 500
    assert db.query(Source).count() == db.query(Comment).count() == db.query(Mention).count() == 0


def test_missing_key_response(api, db, monkeypatch):
    app.dependency_overrides.pop(youtube_router.get_youtube_client)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    response = api.post("/youtube/ingest", json={"video_url": VIDEO_ID})
    assert response.status_code == 503
    assert response.json()["detail"] == "YOUTUBE_API_KEY is not configured on the backend"
    assert db.query(Source).count() == 0


def test_invalid_body_makes_no_google_call(api, google):
    response = api.post("/youtube/ingest", json={"video_url": "https://example.invalid/video"})
    assert response.status_code == 422
    assert google["calls"] == []


@pytest.mark.parametrize("constraint,expected", [
    ("uq_sources_platform_source_id", 409),
    ("uq_comments_source_platform_comment", 409),
    ("uq_mentions_comment_politician", 409),
    ("unrelated_integrity_error", 500),
])
def test_concurrent_constraint_errors_are_classified(api, monkeypatch, constraint, expected):
    def fail(*args, **kwargs):
        original = Exception("Database conflict")
        original.diag = SimpleNamespace(constraint_name=constraint)
        raise IntegrityError("INSERT", {}, original)

    monkeypatch.setattr(youtube_router, "ingest_youtube_video", fail)
    response = api.post("/youtube/ingest", json={"video_url": VIDEO_ID})
    assert response.status_code == expected
