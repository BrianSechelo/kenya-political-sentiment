"""Backfill and politician-create regressions using isolated real ORM/API calls."""

from types import SimpleNamespace

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
from app.routers import mentions as mentions_router


@pytest.fixture
def backfill_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

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
    with Session(engine, autoflush=False) as db:
        db.add_all([
            Politician(politician_id=1, full_name="William Ruto", slug="william-ruto"),
            Source(source_id=4, platform="youtube", platform_source_id="video-one",
                   title="Political debate", source_url="https://youtu.be/video-one"),
            Source(source_id=9, platform="youtube", platform_source_id="video-two",
                   title="Other debate", source_url="https://youtu.be/video-two"),
            Source(source_id=10, platform="youtube", platform_source_id="empty-video",
                   title="Empty source", source_url="https://youtu.be/empty-video"),
        ])
        db.flush()
        db.add(PoliticianAlias(politician_id=1, alias="Ruto", alias_type="surname"))
        db.add_all([
            Comment(comment_id=115, source_id=4, platform_comment_id="one",
                    text="Ruto and Arati spoke"),
            Comment(comment_id=118, source_id=4, platform_comment_id="two",
                    text="Shame to arati and team."),
            Comment(comment_id=119, source_id=4, platform_comment_id="three",
                    text="Paul Simba Arati spoke"),
            Comment(comment_id=121, source_id=4, platform_comment_id="four",
                    text="A general discussion"),
            Comment(comment_id=200, source_id=9, platform_comment_id="five", text="Arati"),
        ])
        db.flush()
        db.add(Mention(mention_id=7, comment_id=115, politician_id=1))
        db.flush()
        db.add(SentimentAnalysis(
            analysis_id=11, mention_id=7, model_version="vader-3.3.2",
            sentiment_label="negative", compound_score=-0.1531,
        ))
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def backfill_api(backfill_db):
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: backfill_db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


ARATI = {"full_name": "Paul Simba Arati", "slug": "paul-simba-arati", "is_active": True}


def add_arati(api, *, active=True, alias=True):
    response = api.post("/politicians/", json={**ARATI, "is_active": active})
    assert response.status_code == 201, response.text
    politician_id = response.json()["politician_id"]
    if alias:
        response = api.post("/politician-aliases/", json={
            "politician_id": politician_id, "alias": "Arati", "alias_type": "surname",
        })
        assert response.status_code == 201, response.text
    return politician_id


def test_politician_creation_returns_201_and_duplicate_returns_409(backfill_api, backfill_db):
    politician_id = add_arati(backfill_api)
    assert backfill_db.get(Politician, politician_id).full_name == ARATI["full_name"]
    duplicate = backfill_api.post("/politicians/", json=ARATI)
    assert duplicate.status_code == 409
    assert backfill_db.query(Politician).filter_by(slug=ARATI["slug"]).count() == 1


def test_registry_addition_backfills_existing_comments_and_preserves_predictions(
    backfill_api, backfill_db, monkeypatch
):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    # Already processed comments still need rescanning after registry changes.
    before = backfill_api.post("/mentions/backfill", json={"source_id": 4})
    assert before.status_code == 200
    assert before.json()["processed_count"] == 4
    assert before.json()["mentions_created"] == 0

    politician_id = add_arati(backfill_api)
    result = backfill_api.post("/mentions/backfill", json={"source_id": 4})
    assert result.status_code == 200, result.text
    assert result.json() == {
        "source_id": 4, "processed_count": 4, "comments_with_new_mentions": 3,
        "mentions_created": 3, "next_after_comment_id": None, "has_more": False,
    }
    # A second politician is added even to comment 115, which already had Ruto.
    detail = backfill_api.get("/comments/115").json()
    assert {row["politician_id"] for row in detail["mentions"]} == {1, politician_id}
    assert backfill_db.get(Comment, 115).text == "Ruto and Arati spoke"
    assert backfill_db.query(Comment).count() == 5
    assert backfill_db.query(SentimentAnalysis).count() == 1
    assert float(backfill_db.get(SentimentAnalysis, 11).compound_score) == -0.1531
    assert backfill_db.query(Mention).filter_by(comment_id=200).count() == 0

    repeated = backfill_api.post("/mentions/backfill", json={"source_id": 4}).json()
    assert repeated["processed_count"] == 4
    assert repeated["mentions_created"] == repeated["comments_with_new_mentions"] == 0
    assert backfill_db.query(Mention).count() == 4

    analysed = backfill_api.post("/sentiment-analyses/analyze-pending").json()
    assert analysed["processed_count"] == 3
    assert backfill_api.post("/sentiment-analyses/analyze-pending").json()["processed_count"] == 0


def test_cursor_covers_all_comments_without_skipping_or_repeating(backfill_api, backfill_db):
    add_arati(backfill_api)
    cursor = 0
    totals = []
    for expected_last in [118, None]:
        response = backfill_api.post("/mentions/backfill", json={
            "source_id": 4, "limit": 2, "after_comment_id": cursor,
        })
        assert response.status_code == 200
        data = response.json()
        totals.append(data["mentions_created"])
        assert data["processed_count"] == 2
        assert data["next_after_comment_id"] == expected_last
        assert data["has_more"] is (expected_last is not None)
        cursor = data["next_after_comment_id"]
    assert totals == [2, 1]
    assert backfill_db.query(Mention).count() == 4


def test_inactive_registry_does_not_add_mentions_or_remove_historical_ones(
    backfill_api, backfill_db
):
    politician_id = add_arati(backfill_api, active=False)
    backfill_db.add(Mention(comment_id=118, politician_id=politician_id))
    backfill_db.commit()
    result = backfill_api.post("/mentions/backfill", json={"source_id": 4}).json()
    assert result["mentions_created"] == 0
    assert backfill_db.query(Mention).filter_by(comment_id=118).count() == 1


def test_full_name_matches_without_surname_alias(backfill_api, backfill_db):
    add_arati(backfill_api, alias=False)
    result = backfill_api.post("/mentions/backfill", json={"source_id": 4}).json()
    assert result["mentions_created"] == 1
    assert backfill_db.query(Mention).filter_by(comment_id=119).count() == 1
    assert backfill_db.query(Mention).filter_by(comment_id=118).count() == 0


def test_ambiguous_alias_does_not_match_but_unique_full_name_still_does(backfill_api, backfill_db):
    add_arati(backfill_api)
    other = backfill_api.post("/politicians/", json={
        "full_name": "Different Person", "slug": "different-person",
    }).json()["politician_id"]
    backfill_api.post("/politician-aliases/", json={
        "politician_id": other, "alias": "ARATI", "alias_type": "surname",
    })
    result = backfill_api.post("/mentions/backfill", json={"source_id": 4}).json()
    assert result["mentions_created"] == 1
    assert backfill_db.query(Mention).filter_by(comment_id=118).count() == 0


@pytest.mark.parametrize("body", [
    {"source_id": 10}, {"source_id": 4, "after_comment_id": 999},
])
def test_empty_or_exhausted_source_returns_zero(backfill_api, body):
    response = backfill_api.post("/mentions/backfill", json=body)
    assert response.status_code == 200
    assert response.json()["processed_count"] == response.json()["mentions_created"] == 0
    assert response.json()["has_more"] is False
    assert response.json()["next_after_comment_id"] is None


def test_missing_source_returns_404(backfill_api):
    response = backfill_api.post("/mentions/backfill", json={"source_id": 999})
    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


@pytest.mark.parametrize("body", [
    {}, {"source_id": 0}, {"source_id": True},
    {"source_id": 4, "after_comment_id": -1},
    {"source_id": 4, "after_comment_id": None},
    {"source_id": 4, "limit": 0}, {"source_id": 4, "limit": 501},
    {"source_id": 4, "limit": True}, {"source_id": 4, "limit": 1.5},
])
def test_invalid_requests_are_rejected_without_writes(backfill_api, backfill_db, body):
    assert backfill_api.post("/mentions/backfill", json=body).status_code == 422
    assert backfill_db.query(Mention).count() == 1


def test_failure_after_staging_rolls_back_entire_batch(backfill_api, backfill_db, monkeypatch):
    politician_id = add_arati(backfill_api)

    def fail(db, request):
        db.add(Mention(comment_id=118, politician_id=politician_id))
        db.flush()
        raise RuntimeError("Simulated batch failure")

    monkeypatch.setattr(mentions_router, "backfill_mentions", fail)
    response = backfill_api.post("/mentions/backfill", json={"source_id": 4})
    assert response.status_code == 500
    assert backfill_db.query(Mention).count() == 1
    assert backfill_db.query(SentimentAnalysis).count() == 1


@pytest.mark.parametrize("constraint,status", [
    ("uq_mentions_comment_politician", 409), ("unrelated_constraint", 500),
])
def test_commit_failure_is_rolled_back_and_classified(
    backfill_api, backfill_db, monkeypatch, constraint, status
):
    add_arati(backfill_api)

    def fail_commit():
        original = Exception("Internal database details")
        original.diag = SimpleNamespace(constraint_name=constraint)
        raise IntegrityError("INSERT", {}, original)

    monkeypatch.setattr(backfill_db, "commit", fail_commit)
    response = backfill_api.post("/mentions/backfill", json={"source_id": 4})
    assert response.status_code == status
    assert "Internal database details" not in response.text
    assert backfill_db.query(Mention).count() == 1


def test_manual_mention_endpoint_still_works(backfill_api, backfill_db):
    politician_id = add_arati(backfill_api)
    response = backfill_api.post("/mentions/", json={
        "comment_id": 118, "politician_id": politician_id,
    })
    assert response.status_code == 201
    assert backfill_api.post("/mentions/", json={
        "comment_id": 118, "politician_id": politician_id,
    }).status_code == 409
    result = backfill_api.post("/mentions/backfill", json={"source_id": 4}).json()
    assert result["mentions_created"] == 2