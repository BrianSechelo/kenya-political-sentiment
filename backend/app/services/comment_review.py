"""Read stored comments and batch-load their source, mentions, and predictions."""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import Comment, Mention, Politician, SentimentAnalysis, Source
from app.schemas.comment import (
    CommentListResponse,
    CommentMentionResponse,
    CommentReviewResponse,
    CommentSourceResponse,
)
from app.schemas.sentiment_analysis import SentimentAnalysisResponse


def build_comment_reviews(
    db: Session, comments: list[Comment]
) -> list[CommentReviewResponse]:
    if not comments:
        return []

    sources = {
        source.source_id: CommentSourceResponse.model_validate(source)
        for source in db.query(Source).filter(
            Source.source_id.in_({comment.source_id for comment in comments})
        ).all()
    }
    mention_rows = (
        db.query(Mention, Politician)
        .join(Politician, Politician.politician_id == Mention.politician_id)
        .filter(Mention.comment_id.in_([comment.comment_id for comment in comments]))
        .order_by(Mention.mention_id)
        .all()
    )
    analyses_by_mention = defaultdict(list)
    if mention_rows:
        analyses = (
            db.query(SentimentAnalysis)
            .filter(SentimentAnalysis.mention_id.in_(
                [mention.mention_id for mention, _ in mention_rows]
            ))
            .order_by(SentimentAnalysis.model_version, SentimentAnalysis.analysis_id)
            .all()
        )
        for analysis in analyses:
            analyses_by_mention[analysis.mention_id].append(
                SentimentAnalysisResponse.model_validate(analysis)
            )

    mentions_by_comment = defaultdict(list)
    for mention, politician in mention_rows:
        mentions_by_comment[mention.comment_id].append(CommentMentionResponse(
            mention_id=mention.mention_id,
            politician_id=politician.politician_id,
            full_name=politician.full_name,
            slug=politician.slug,
            relevance_score=mention.relevance_score,
            detected_at=mention.detected_at,
            analyses=analyses_by_mention[mention.mention_id],
        ))

    return [CommentReviewResponse(
        comment_id=comment.comment_id,
        source_id=comment.source_id,
        platform_comment_id=comment.platform_comment_id,
        parent_comment_id=comment.parent_comment_id,
        text=comment.text,
        like_count=comment.like_count,
        published_at=comment.published_at,
        collected_at=comment.collected_at,
        source=sources[comment.source_id],
        mentions=mentions_by_comment[comment.comment_id],
    ) for comment in comments]


def list_comment_reviews(
    db: Session,
    *,
    source_id: int | None,
    politician_id: int | None,
    has_mentions: bool | None,
    skip: int,
    limit: int,
) -> CommentListResponse:
    query = db.query(Comment)
    if source_id is not None:
        query = query.filter(Comment.source_id == source_id)
    # EXISTS keeps pagination and totals at comment level, even when a comment
    # has multiple mentions or model predictions.
    mentions = db.query(Mention).filter(Mention.comment_id == Comment.comment_id)
    if politician_id is not None:
        query = query.filter(
            mentions.filter(Mention.politician_id == politician_id).exists()
        )
    if has_mentions is not None:
        exists = mentions.exists()
        query = query.filter(exists if has_mentions else ~exists)

    total = query.count()
    comments = (
        query.order_by(Comment.collected_at.desc(), Comment.comment_id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return CommentListResponse(
        total=total, skip=skip, limit=limit,
        items=build_comment_reviews(db, comments),
    )
