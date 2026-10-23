from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.youtube_client import extract_video_id


class YouTubeIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_url: str = Field(
        min_length=1,
        max_length=2048,
        description="A YouTube watch, short, live or youtu.be URL, or an 11-character video ID.",
    )
    max_comments: int = Field(
        default=100,
        strict=True,
        ge=1,
        le=500,
        description="Maximum top-level comments examined, including existing comments.",
    )
    page_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Use the previous response's next_page_token to continue the same video.",
    )

    @field_validator("video_url")
    @classmethod
    def validate_video_url(cls, value: str) -> str:
        value = value.strip()
        extract_video_id(value)
        return value


class YouTubeIngestResponse(BaseModel):
    video_id: str
    source_id: int
    source_created: bool
    video_title: str
    comment_scope: Literal["top_level"] = "top_level"
    fetched_count: int
    created_count: int
    existing_count: int
    skipped_count: int
    mentions_created: int
    pages_fetched: int
    next_page_token: str | None
    has_more: bool
