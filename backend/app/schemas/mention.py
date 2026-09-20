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