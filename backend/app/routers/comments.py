from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Comment, Source
from app.schemas.comment import (
    CommentCreate,
    CommentListResponse,
    CommentResponse,
    CommentReviewResponse,
)
from app.services.comment_review import build_comment_reviews, list_comment_reviews
from app.services.mention_detection import create_mentions_for_comment


router = APIRouter(prefix="/comments", tags=["Comments"])

@router.get(
    "/",
    response_model=CommentListResponse,
    summary="Review collected comments and stored sentiment results",
    description=(
        "Filter saved comments by source, politician, or mention presence. "
        "Returns each comment once, newest collected first, with its source, "
        "all politician mentions, and all stored model predictions. "
        "A politician filter selects comments without hiding their other mentions. "
        "Empty analyses means that mention has no stored prediction. "
        "VADER predictions score the whole comment, not sentiment specifically "
        "toward each mentioned politician. "
        "This endpoint does not collect data or run sentiment analysis."
    ),
)
def list_comments(
    source_id: int | None = Query(default=None, gt=0),
    politician_id: int | None = Query(default=None, gt=0),
    has_mentions: bool | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return list_comment_reviews(
        db, source_id=source_id, politician_id=politician_id,
        has_mentions=has_mentions, skip=skip, limit=limit,
    )


@router.get(
    "/{comment_id}",
    response_model=CommentReviewResponse,
    summary="Review one collected comment",
    responses={404: {"description": "Comment not found"}},
)
def get_comment(
    comment_id: int = Path(gt=0),
    db: Session = Depends(get_db),
):
    comment = db.get(Comment, comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    return build_comment_reviews(db, [comment])[0]


@router.post(
    "/",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Source not found"},
        status.HTTP_409_CONFLICT: {
            "description": "Comment already exists for this source",
        },
    },
)
def create_comment(
    comment_data: CommentCreate,
    db: Session = Depends(get_db),
):
    source = (
        db.query(Source)
        .filter(Source.source_id == comment_data.source_id)
        .first()
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    existing_comment = (
        db.query(Comment)
        .filter(
            Comment.source_id == comment_data.source_id,
            Comment.platform_comment_id == comment_data.platform_comment_id,
        )
        .first()
    )
    if existing_comment is not None:
        raise HTTPException(
            status_code=409, detail="Comment already exists for this source"
        )

    new_comment = Comment(**comment_data.model_dump())
    try:
        db.add(new_comment)
        db.flush()  # Obtain comment_id without committing the comment yet.
        create_mentions_for_comment(db, new_comment)
        db.commit()  # Save the comment and all detected mentions together.
    except IntegrityError as exc:
        db.rollback()
        # PostgreSQL exposes the violated constraint via psycopg/psycopg2.
        # Handle a duplicate arriving between the pre-check and INSERT, but
        # don't disguise a different integrity error as a duplicate comment.
        diagnostic = getattr(exc.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == (
            "uq_comments_source_platform_comment"
        ):
            raise HTTPException(
                status_code=409,
                detail="Comment already exists for this source",
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise

    db.refresh(new_comment)
    return new_comment
