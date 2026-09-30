from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Comment, Mention, Politician, SentimentAnalysis
from app.schemas.sentiment_analysis import (
    PoliticianSentimentSummaryResponse,
    SentimentAnalysisBatchResponse,
    SentimentAnalysisCreate,
    SentimentAnalysisResponse,
    SentimentSummaryResponse,
    )
from app.services.sentiment_service import MODEL_VERSION, analyze_sentiment

router = APIRouter(
    prefix="/sentiment-analyses",
    tags=["Sentiment Analyses"],
)

def get_summary_values(
    db: Session,
    model_version: str,
    politician_id: int | None = None,
) -> dict:
    query = db.query(
        func.count(SentimentAnalysis.analysis_id).label("total_analyses"),
        func.coalesce(
            func.sum(
                case(
                    (SentimentAnalysis.sentiment_label == "positive", 1),
                    else_=0,
                )
            ),
            0,
        ).label("positive_count"),
        func.coalesce(
            func.sum(
                case(
                    (SentimentAnalysis.sentiment_label == "negative", 1),
                    else_=0,
                )
            ),
            0,
        ).label("negative_count"),
        func.coalesce(
            func.sum(
                case(
                    (SentimentAnalysis.sentiment_label == "neutral", 1),
                    else_=0,
                )
            ),
            0,
        ).label("neutral_count"),
        func.avg(SentimentAnalysis.compound_score).label(
            "average_compound_score"
        ),
    )

    if politician_id is not None:
        query = query.join(
            Mention,
            Mention.mention_id == SentimentAnalysis.mention_id,
        ).filter(Mention.politician_id == politician_id)

    result = query.filter(
        SentimentAnalysis.model_version == model_version
    ).one()

    total = int(result.total_analyses or 0)
    positive = int(result.positive_count or 0)
    negative = int(result.negative_count or 0)
    neutral = int(result.neutral_count or 0)

    def percentage(count: int) -> float:
        return round((count / total) * 100, 2) if total else 0.0

    return {
        "model_version": model_version,
        "total_analyses": total,
        "positive_count": positive,
        "negative_count": negative,
        "neutral_count": neutral,
        "positive_percentage": percentage(positive),
        "negative_percentage": percentage(negative),
        "neutral_percentage": percentage(neutral),
        "average_compound_score": (
            round(float(result.average_compound_score), 4)
            if result.average_compound_score is not None
            else None
        ),
    }

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

@router.get(
    "/summary",
    response_model=SentimentSummaryResponse,
)
def get_sentiment_summary(
    model_version: str = Query(default=MODEL_VERSION, min_length=1),
    db: Session = Depends(get_db),
):
    return get_summary_values(db, model_version)

@router.get(
    "/summary/politicians/{politician_id}",
    response_model=PoliticianSentimentSummaryResponse,
)
def get_politician_sentiment_summary(
    politician_id: int,
    model_version: str = Query(default=MODEL_VERSION, min_length=1),
    db: Session = Depends(get_db),
):
    politician = (
        db.query(Politician)
        .filter(Politician.politician_id == politician_id)
        .first()
    )

    if politician is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Politician not found",
        )

    summary = get_summary_values(
        db=db,
        model_version=model_version,
        politician_id=politician_id,
    )

    return PoliticianSentimentSummaryResponse(
        politician_id=politician.politician_id,
        full_name=politician.full_name,
        **summary,
    )