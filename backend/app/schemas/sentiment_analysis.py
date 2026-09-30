from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SentimentAnalysisBase(BaseModel):
    mention_id: int = Field(gt=0)
    model_version: str = Field(min_length=1, max_length=100)
    sentiment_label: Literal["positive", "negative", "neutral"]
    confidence_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    compound_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
    )


class SentimentAnalysisCreate(SentimentAnalysisBase):
    pass


class SentimentAnalysisResponse(SentimentAnalysisBase):
    analysis_id: int
    analyzed_at: datetime

    model_config = ConfigDict(from_attributes=True)

class SentimentAnalysisBatchResponse(BaseModel):
    model_version: str
    processed_count: int
    skipped_count: int
    analyses: list[SentimentAnalysisResponse]

class SentimentSummaryResponse(BaseModel):
    model_version: str
    total_analyses: int
    positive_count: int
    negative_count: int
    neutral_count: int
    positive_percentage: float
    negative_percentage: float
    neutral_percentage: float
    average_compound_score: float | None


class PoliticianSentimentSummaryResponse(SentimentSummaryResponse):
    politician_id: int
    full_name: str