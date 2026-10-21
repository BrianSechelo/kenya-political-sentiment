from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Comment, Source
from app.schemas.comment import CommentCreate, CommentResponse
from app.services.mention_detection import create_mentions_for_comment


router = APIRouter(prefix="/comments", tags=["Comments"])


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