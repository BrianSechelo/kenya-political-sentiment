from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Mention, SentimentAnalysis
from app.schemas.sentiment_analysis import (
    SentimentAnalysisCreate,
    SentimentAnalysisResponse,
)


router = APIRouter(
    prefix="/sentiment-analyses",
    tags=["Sentiment Analyses"],
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