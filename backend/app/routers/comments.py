from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Comment, Source
from app.schemas.comment import CommentCreate, CommentResponse, CommentUpdate


router = APIRouter(
    prefix="/comments",
    tags=["Comments"],
)

@router.post(
    "/",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Source not found",
        },
        status.HTTP_409_CONFLICT: {
            "description": "Comment already exists for this source",
        },
    },
)
def create_comment(
    comment_data: CommentCreate,
    db: Session = Depends(get_db),
):
    # Make sure the source exists
    source = (
        db.query(Source)
        .filter(Source.source_id == comment_data.source_id)
        .first()
    )

    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found",
        )

    # Check whether this comment already exists for this source
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
            status_code=status.HTTP_409_CONFLICT,
            detail="Comment already exists for this source",
        )

    # Create the comment
    new_comment = Comment(**comment_data.model_dump())

    db.add(new_comment)
    db.commit()
    db.refresh(new_comment)

    return new_comment