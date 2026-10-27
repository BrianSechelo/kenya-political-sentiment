"""Review API integration tests: isolated SQLite, actual ORM and response models."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, MetaData, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Comment, Mention, Politician, SentimentAnalysis, Source


@pytest.fixture
def review_db():
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
        yield db
    engine.dispose()


@pytest.fixture
def review_api(review_db):
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: review_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.fixture
def seeded(review_db):
    db = review_db
    db.add_all([
        Source(source_id=1, platform="youtube", platform_source_id="video-one",
               title="Political debate", source_url="https://youtu.be/video-one",
               raw_data={"private_source_field": "omit-me"}),
        Source(source_id=2, platform="youtube", platform_source_id="video-two",
               title="Another debate", source_url="https://youtu.be/video-two"),
        Politician(politician_id=1, full_name="William Ruto", slug="william-ruto"),
        # Historical mentions should remain visible after a politician is inactive.
        Politician(politician_id=8, full_name="Raila Odinga", slug="raila-odinga",
                   is_active=False),
    ])
    db.flush()
    moment = datetime(2026, 10, 23, 8)
    db.add_all([
        Comment(comment_id=101, source_id=1, platform_comment_id="one",
                text="Ruto spoke", collected_at=moment, published_at=None),
        Comment(comment_id=102, source_id=1, platform_comment_id="two",
                text="Ruto and Raila 😂 Kiswahili/Sheng", collected_at=moment,
                like_count=0, raw_data={"private_comment_field": "omit-me"},
                author_hash="private-hash"),
        Comment(comment_id=103, source_id=1, platform_comment_id="three",
                text="General discussion", collected_at=moment),
        Comment(comment_id=201, source_id=2, platform_comment_id="four",
                text="Raila spoke", collected_at=moment + timedelta(hours=1)),
    ])
    db.flush()
    db.add_all([
        Mention(mention_id=1, comment_id=101, politician_id=1),
        Mention(mention_id=2, comment_id=102, politician_id=1),
        Mention(mention_id=3, comment_id=102, politician_id=8),
        Mention(mention_id=4, comment_id=201, politician_id=8),
    ])
    db.flush()
    db.add_all([
        SentimentAnalysis(analysis_id=1, mention_id=2, model_version="vader-3.3.2",
                          sentiment_label="positive", compound_score=0.8271,
                          confidence_score=0.284),
        SentimentAnalysis(analysis_id=2, mention_id=2, model_version="comparison-v1",
                          sentiment_label="negative", compound_score=None),
        SentimentAnalysis(analysis_id=3, mention_id=4, model_version="vader-3.3.2",
                          sentiment_label="neutral", compound_score=0),
    ])
    db.commit()
    return db


def ids(response):
    assert response.status_code == 200, response.text
    return [item["comment_id"] for item in response.json()["items"]]


def test_empty_database_returns_page_not_error(review_api):
    assert review_api.get("/comments/").json() == {
        "total": 0, "skip": 0, "limit": 50, "items": []
    }


def test_source_filter_keeps_unmatched_comments_and_counts_comments(review_api, seeded):
    response = review_api.get("/comments/", params={"source_id": 1})
    assert ids(response) == [103, 102, 101]
    assert response.json()["total"] == 3
    assert response.json()["items"][0]["mentions"] == []


def test_pagination_has_stable_ties_no_duplicates_from_mentions_or_models(review_api, seeded):
    first = review_api.get("/comments/", params={"limit": 2})
    second = review_api.get("/comments/", params={"skip": 2, "limit": 2})
    assert ids(first) == [201, 103]
    assert ids(second) == [102, 101]
    assert first.json()["total"] == second.json()["total"] == 4
    beyond = review_api.get("/comments/", params={"skip": 100})
    assert ids(beyond) == []
    assert beyond.json()["total"] == 4


@pytest.mark.parametrize("params,expected", [
    ({"politician_id": 1}, [102, 101]),
    ({"politician_id": 8}, [201, 102]),
    ({"source_id": 1, "politician_id": 8}, [102]),
    ({"has_mentions": "true"}, [201, 102, 101]),
    ({"has_mentions": "false"}, [103]),
    ({"politician_id": 1, "has_mentions": "false"}, []),
    ({"source_id": 999}, []),
    ({"politician_id": 999}, []),
])
def test_filters_combine_and_unknown_filters_return_empty(review_api, seeded, params, expected):
    response = review_api.get("/comments/", params=params)
    assert ids(response) == expected
    assert response.json()["total"] == len(expected)


def test_detail_preserves_text_and_links_only_own_predictions(review_api, seeded):
    response = review_api.get("/comments/102")
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == "Ruto and Raila 😂 Kiswahili/Sheng"
    assert data["like_count"] == 0
    assert data["published_at"] is None
    assert data["source"]["title"] == "Political debate"
    assert [mention["politician_id"] for mention in data["mentions"]] == [1, 8]
    ruto, raila = data["mentions"]
    assert ruto["full_name"] == "William Ruto"
    assert [row["model_version"] for row in ruto["analyses"]] == [
        "comparison-v1", "vader-3.3.2"
    ]
    assert ruto["analyses"][0]["compound_score"] is None
    assert ruto["analyses"][1]["compound_score"] == pytest.approx(0.8271)
    assert raila["analyses"] == []  # Not copied from the other Raila comment.
    assert "raw_data" not in data and "author_hash" not in data
    assert "raw_data" not in data["source"]
    assert "private-" not in response.text and "omit-me" not in response.text


def test_politician_filter_does_not_hide_other_mentions(review_api, seeded):
    response = review_api.get("/comments/", params={"politician_id": 1})
    assert len(response.json()["items"][0]["mentions"]) == 2


def test_unanalysed_mention_and_unmatched_detail_remain_visible(review_api, seeded):
    pending = review_api.get("/comments/101").json()
    assert pending["mentions"][0]["analyses"] == []
    assert review_api.get("/comments/103").json()["mentions"] == []
    assert review_api.get("/comments/999").status_code == 404


@pytest.mark.parametrize("path", [
    "/comments/?limit=0", "/comments/?limit=101", "/comments/?skip=-1",
    "/comments/?source_id=0", "/comments/?politician_id=-1",
    "/comments/?has_mentions=invalid", "/comments/0", "/comments/not-an-id",
])
def test_invalid_parameters_are_rejected(review_api, path):
    assert review_api.get(path).status_code == 422


def test_reads_do_not_create_mentions_or_analyses(review_api, seeded):
    before = tuple(seeded.query(model).count() for model in (Comment, Mention, SentimentAnalysis))
    review_api.get("/comments/")
    review_api.get("/comments/102")
    after = tuple(seeded.query(model).count() for model in (Comment, Mention, SentimentAnalysis))
    assert before == after == (4, 4, 3)


def test_post_still_saves_comment_and_detects_mentions(review_api, seeded):
    response = review_api.post("/comments/", json={
        "source_id": 1, "platform_comment_id": "new", "text": "William Ruto spoke today",
    })
    assert response.status_code == 201
    comment_id = response.json()["comment_id"]
    detail = review_api.get(f"/comments/{comment_id}").json()
    assert detail["mentions"][0]["politician_id"] == 1
    assert detail["mentions"][0]["analyses"] == []


def test_review_query_count_is_bounded_by_page_not_comment_count(review_api, seeded):
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(seeded.bind, "before_cursor_execute", record)
    try:
        assert len(ids(review_api.get("/comments/"))) == 4
    finally:
        event.remove(seeded.bind, "before_cursor_execute", record)
    assert len(statements) <= 5
