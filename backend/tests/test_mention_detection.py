"""Isolated SQLite tests; never connect to or mutate the configured database.

The application's model mappings are reused. Only the test DDL substitutes
SQLite JSON for PostgreSQL JSONB. PostgreSQL concurrency is not exercised here.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON, MetaData, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Comment, Mention, Politician, PoliticianAlias, Source
from app.routers import comments as comments_router
from app.schemas.comment import CommentCreate
from app.services.mention_detection import (
    create_mentions_for_comment,
    match_politician_ids,
)


@pytest.mark.parametrize(
    "text,names,expected",
    [
        ("WILLIAM   RUTO did well", [(1, "William Ruto")], [1]),
        ("#Ruto, and Raila!", [(1, "Ruto"), (8, "Raila")], [1, 8]),
        ("Ruto Ruto William Ruto", [(1, "Ruto"), (1, "William Ruto")], [1]),
        ("Rutos and Maruto", [(1, "Ruto")], []),
        ("William Ruto", [(1, "William")], [1]),
        ("William and Ruto", [(1, "William Ruto")], []),
        ("ＲＵＴＯ", [(1, "Ruto")], [1]),
        ("Ruto's speech", [(1, "Ruto")], [1]),
        ("Baba spoke", [(1, "Baba"), (8, "baba")], []),
        ("William Ruto, Baba", [(1, "William Ruto"), (1, "Baba"), (8, "Baba")], [1]),
        ("", [(1, "Ruto")], []),
        ("Something unrelated", [(1, "Ruto"), (1, "!!!")], []),
        ("Kiongozi wa watu", [(1, "kiongozi wa watu")], [1]),
    ],
)
def test_matching_rules(text, names, expected):
    assert match_politician_ids(text, names) == expected


@pytest.fixture
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for model in (Politician, PoliticianAlias, Source, Comment, Mention):
        table = model.__table__.to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
    metadata.create_all(engine)

    with Session(engine, autoflush=False) as session:
        session.add_all([
            Politician(politician_id=1, full_name="William Ruto", slug="william-ruto"),
            Politician(politician_id=8, full_name="Raila Odinga", slug="raila-odinga"),
            Politician(politician_id=9, full_name="Inactive Person", slug="inactive", is_active=False),
            Source(source_id=1, platform="youtube", platform_source_id="test-video",
                   title="Test source", source_url="https://example.invalid/test"),
        ])
        session.flush()
        session.add_all([
            PoliticianAlias(politician_id=1, alias="Ruto", alias_type="surname"),
            PoliticianAlias(politician_id=8, alias="Baba", alias_type="nickname"),
            PoliticianAlias(politician_id=9, alias="Hidden", alias_type="nickname"),
        ])
        session.commit()
        yield session
    engine.dispose()


def payload(text="William Ruto did well", external_id="test-comment", source_id=1):
    return CommentCreate(source_id=source_id, platform_comment_id=external_id,
                         text=text, raw_data={"is_test": True})


def test_comment_creates_multiple_mentions_and_preserves_response(db):
    comment = comments_router.create_comment(payload("Ruto and Baba spoke"), db)
    rows = db.query(Mention).filter_by(comment_id=comment.comment_id).all()
    assert {row.politician_id for row in rows} == {1, 8}
    assert all(row.relevance_score is None for row in rows)
    assert comment.raw_data == {"is_test": True}
    assert db.query(Comment).count() == 1


def test_no_match_still_saves_comment(db):
    comment = comments_router.create_comment(payload("Unrelated discussion"), db)
    assert comment.comment_id is not None
    assert db.query(Mention).count() == 0


def test_inactive_politicians_and_aliases_are_excluded(db):
    comments_router.create_comment(payload("Inactive Person Hidden"), db)
    assert db.query(Mention).count() == 0


def test_repeat_detection_is_safe_with_autoflush_disabled(db):
    comment = Comment(source_id=1, platform_comment_id="repeat", text="Ruto Baba")
    db.add(comment)
    db.flush()
    assert len(create_mentions_for_comment(db, comment)) == 2
    assert create_mentions_for_comment(db, comment) == []
    db.commit()
    assert db.query(Mention).count() == 2


def test_existing_manual_mention_is_preserved(db):
    comment = Comment(source_id=1, platform_comment_id="manual", text="Ruto")
    db.add(comment)
    db.flush()
    manual = Mention(comment_id=comment.comment_id, politician_id=1,
                     relevance_score=Decimal("0.7"))
    db.add(manual)
    db.commit()
    assert create_mentions_for_comment(db, comment) == []
    assert db.query(Mention).one().relevance_score == Decimal("0.7000")


def test_duplicate_comment_returns_409_without_extra_rows(db):
    comments_router.create_comment(payload(), db)
    with pytest.raises(HTTPException) as caught:
        comments_router.create_comment(payload(), db)
    assert caught.value.status_code == 409
    assert db.query(Comment).count() == 1
    assert db.query(Mention).count() == 1


def test_missing_source_returns_404(db):
    with pytest.raises(HTTPException) as caught:
        comments_router.create_comment(payload(source_id=999), db)
    assert caught.value.status_code == 404
    assert db.query(Comment).count() == 0


def test_detector_failure_rolls_back_comment_and_staged_mention(db, monkeypatch):
    def fail_after_staging(session, comment):
        session.add(Mention(comment_id=comment.comment_id, politician_id=1))
        session.flush()
        raise RuntimeError("simulated detector failure")

    monkeypatch.setattr(comments_router, "create_mentions_for_comment", fail_after_staging)
    with pytest.raises(RuntimeError, match="simulated detector failure"):
        comments_router.create_comment(payload(), db)
    assert db.query(Comment).count() == 0
    assert db.query(Mention).count() == 0


def test_unflushed_comment_is_rejected(db):
    with pytest.raises(ValueError, match="Flush"):
        create_mentions_for_comment(db, Comment(source_id=1, text="Ruto"))


@pytest.mark.parametrize("constraint,expected_status", [
    ("uq_comments_source_platform_comment", 409),
    ("a_different_constraint", None),
])
def test_postgres_integrity_error_is_classified_by_constraint(constraint, expected_status):
    session = Mock()
    session.query.return_value.filter.return_value.first.side_effect = [object(), None]
    original = Exception("integrity failure")
    original.diag = SimpleNamespace(constraint_name=constraint)
    failure = IntegrityError("INSERT", {}, original)
    session.flush.side_effect = failure
    if expected_status:
        with pytest.raises(HTTPException) as caught:
            comments_router.create_comment(payload(), session)
        assert caught.value.status_code == expected_status
    else:
        with pytest.raises(IntegrityError):
            comments_router.create_comment(payload(), session)
    session.rollback.assert_called_once()
    session.commit.assert_not_called()
