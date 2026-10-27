from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.sentiment_analysis import SentimentAnalysisResponse


class CommentBase(BaseModel):
    platform_comment_id: str
    parent_comment_id: str | None = None
    text: str
    author_hash: str | None = None
    like_count: int | None = None
    published_at: datetime | None = None
    raw_data: dict[str, Any] | None = None


class CommentCreate(CommentBase):
    source_id: int


class CommentUpdate(BaseModel):
    text: str | None = None
    like_count: int | None = None
    raw_data: dict[str, Any] | None = None


class CommentResponse(CommentBase):
    comment_id: int
    source_id: int
    collected_at: datetime

    model_config = ConfigDict(from_attributes=True)

class CommentSourceResponse(BaseModel):
    source_id: int
    platform: str
    platform_source_id: str
    title: str
    source_url: str
    channel_name: str | None

    model_config = ConfigDict(from_attributes=True)


class CommentMentionResponse(BaseModel):
    mention_id: int
    politician_id: int
    full_name: str
    slug: str
    relevance_score: float | None
    detected_at: datetime
    analyses: list[SentimentAnalysisResponse] = Field(default_factory=list)


class CommentReviewResponse(BaseModel):
    comment_id: int
    source_id: int
    platform_comment_id: str
    parent_comment_id: str | None
    text: str
    like_count: int | None
    published_at: datetime | None
    collected_at: datetime
    source: CommentSourceResponse
    mentions: list[CommentMentionResponse] = Field(default_factory=list)

class CommentListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    items: list[CommentReviewResponse]
