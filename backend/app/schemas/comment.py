from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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