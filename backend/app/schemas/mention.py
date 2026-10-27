from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MentionCreate(BaseModel):
    comment_id: int
    politician_id: int
    relevance_score: float | None = Field(
        default=None,
        ge=0,
        le=1,
    )


class MentionResponse(MentionCreate):
    mention_id: int
    detected_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MentionBackfillRequest(BaseModel):
    source_id: int = Field(gt=0, strict=True)
    after_comment_id: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=100, ge=1, le=500, strict=True)


class MentionBackfillResponse(BaseModel):
    source_id: int
    processed_count: int
    comments_with_new_mentions: int
    mentions_created: int
    next_after_comment_id: int | None
    has_more: bool
