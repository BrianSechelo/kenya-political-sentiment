import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.youtube import YouTubeIngestRequest, YouTubeIngestResponse
from app.services.youtube_client import YouTubeAPIError, YouTubeClient
from app.services.youtube_ingestion import ingest_youtube_video


router = APIRouter(prefix="/youtube", tags=["YouTube Collection"])


def get_youtube_client() -> YouTubeClient:
    try:
        return YouTubeClient(api_key=os.getenv("YOUTUBE_API_KEY", ""))
    except YouTubeAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None


@router.post(
    "/ingest",
    response_model=YouTubeIngestResponse,
    summary="Collect public top-level comments from one YouTube video",
    description=(
        "Fetch video metadata and up to max_comments top-level comments, ordered newest first. "
        "Saves new comments and automatically detects politician mentions. Repeated comments "
        "are counted as existing. Use next_page_token to continue the same video. "
        "Run /sentiment-analyses/analyze-pending after collection to produce sentiment results."
    ),
    responses={
        404: {"description": "Video unavailable or not found"},
        409: {"description": "Concurrent collection conflict; retry the request"},
        422: {"description": "Invalid input, page token, or disabled comments"},
        502: {"description": "YouTube unavailable or returned an invalid response"},
        503: {"description": "API key missing, access rejected, or quota exhausted"},
    },
)
def collect_youtube_comments(
    request: YouTubeIngestRequest,
    db: Session = Depends(get_db),
    client: YouTubeClient = Depends(get_youtube_client),
):
    try:
        return ingest_youtube_video(db, client, request)
    except YouTubeAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except IntegrityError as exc:
        diagnostic = getattr(exc.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) in {
            "uq_sources_platform_source_id", "uq_comments_source_platform_comment",
            "uq_mentions_comment_politician",
        }:
            raise HTTPException(
                status_code=409,
                detail="Another request collected the same records; retry this request",
            ) from None
        raise
