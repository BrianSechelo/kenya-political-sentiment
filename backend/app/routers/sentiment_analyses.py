from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Comment, Mention, SentimentAnalysis
from app.schemas.sentiment_analysis import (
    SentimentAnalysisBatchResponse,
    SentimentAnalysisCreate,
    SentimentAnalysisResponse,
)
from app.services.sentiment_service import MODEL_VERSION, analyze_sentiment

router = APIRouter(
    prefix="/sentiment-analyses",
    tags=["Sentiment Analyses"],
)

@router.post(
    "/analyze-pending",
    response_model=SentimentAnalysisBatchResponse,
    status_code=status.HTTP_200_OK,
)
def analyze_pending_mentions(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    pending_mentions = (
        db.query(Mention, Comment)
        .join(Comment, Comment.comment_id == Mention.comment_id)
        .outerjoin(
            SentimentAnalysis,
            and_(
                SentimentAnalysis.mention_id == Mention.mention_id,
                SentimentAnalysis.model_version == MODEL_VERSION,
            ),
        )
        .filter(SentimentAnalysis.analysis_id.is_(None))
        .order_by(Mention.mention_id)
        .limit(limit)
        .all()
    )

    new_analyses = []
    skipped_count = 0

    for mention, comment in pending_mentions:
        try:
            prediction = analyze_sentiment(comment.text)
        except ValueError:
            skipped_count += 1
            continue

        analysis = SentimentAnalysis(
            mention_id=mention.mention_id,
            model_version=prediction.model_version,
            sentiment_label=prediction.sentiment_label,
            confidence_score=prediction.confidence_score,
            compound_score=prediction.compound_score,
        )

        db.add(analysis)
        new_analyses.append(analysis)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="One or more sentiment analyses already exist",
        ) from exc

    for analysis in new_analyses:
        db.refresh(analysis)

    return SentimentAnalysisBatchResponse(
        model_version=MODEL_VERSION,
        processed_count=len(new_analyses),
        skipped_count=skipped_count,
        analyses=new_analyses,
    )

@router.post(
    "/",
    response_model=SentimentAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_sentiment_analysis(
    data: SentimentAnalysisCreate,
    db: Session = Depends(get_db),
):
    mention = (
        db.query(Mention)
        .filter(Mention.mention_id == data.mention_id)
        .first()
    )

    if mention is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mention not found",
        )

    existing_analysis = (
        db.query(SentimentAnalysis)
        .filter(
            SentimentAnalysis.mention_id == data.mention_id,
            SentimentAnalysis.model_version == data.model_version,
        )
        .first()
    )

    if existing_analysis is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sentiment analysis already exists for this mention and model",
        )

    new_analysis = SentimentAnalysis(**data.model_dump())

    try:
        db.add(new_analysis)
        db.commit()
        db.refresh(new_analysis)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sentiment analysis already exists for this mention and model",
        )

    return new_analysis

@router.post(
    "/analyze/{mention_id}",
    response_model=SentimentAnalysisResponse,
    status_code=status.HTTP_201_CREATED,
)
def analyze_mention(
    mention_id: int,
    db: Session = Depends(get_db),
):
    mention = (
        db.query(Mention)
        .filter(Mention.mention_id == mention_id)
        .first()
    )

    if mention is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mention not found",
        )

    comment = (
        db.query(Comment)
        .filter(Comment.comment_id == mention.comment_id)
        .first()
    )

    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found",
        )

    try:
        prediction = analyze_sentiment(comment.text)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    existing_analysis = (
        db.query(SentimentAnalysis)
        .filter(
            SentimentAnalysis.mention_id == mention_id,
            SentimentAnalysis.model_version == prediction.model_version,
        )
        .first()
    )

    if existing_analysis is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sentiment analysis already exists for this mention and model",
        )

    new_analysis = SentimentAnalysis(
        mention_id=mention_id,
        model_version=prediction.model_version,
        sentiment_label=prediction.sentiment_label,
        confidence_score=prediction.confidence_score,
         compound_score=prediction.compound_score,
    )

    try:
        db.add(new_analysis)
        db.commit()
        db.refresh(new_analysis)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sentiment analysis already exists for this mention and model",
        ) from exc

    return new_analysis