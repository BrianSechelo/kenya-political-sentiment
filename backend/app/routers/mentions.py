from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Comment, Mention, Politician
from app.schemas.mention import MentionCreate, MentionResponse


router = APIRouter(
    prefix="/mentions",
    tags=["Mentions"],
)


@router.post(
    "/",
    response_model=MentionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Comment or politician not found"},
        409: {"description": "Mention already exists"},
    },
)
def create_mention(
    mention_data: MentionCreate,
    db: Session = Depends(get_db),
):
    comment = (
        db.query(Comment)
        .filter(Comment.comment_id == mention_data.comment_id)
        .first()
    )

    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found",
        )

    politician = (
        db.query(Politician)
        .filter(Politician.politician_id == mention_data.politician_id)
        .first()
    )

    if politician is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Politician not found",
        )

    existing_mention = (
        db.query(Mention)
        .filter(
            Mention.comment_id == mention_data.comment_id,
            Mention.politician_id == mention_data.politician_id,
        )
        .first()
    )

    if existing_mention is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Mention already exists for this comment and politician",
        )

    new_mention = Mention(**mention_data.model_dump())

    db.add(new_mention)
    db.commit()
    db.refresh(new_mention)

    return new_mention