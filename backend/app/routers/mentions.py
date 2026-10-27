from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Comment, Mention, Politician
from app.schemas.mention import (
    MentionBackfillRequest,
    MentionBackfillResponse,
    MentionCreate,
    MentionResponse,
)
from app.services.mention_backfill import backfill_mentions


router = APIRouter(
    prefix="/mentions",
    tags=["Mentions"],
)


@router.post(
    "/backfill",
    response_model=MentionBackfillResponse,
    summary="Detect missing politician mentions in saved comments",
    description=(
        "Rescan one source using active politicians and saved aliases. Adds only "
        "missing comment/politician pairs, including additional politicians in "
        "comments that already have mentions. Preserves existing comments, "
        "mentions, and predictions. Processes comments in ascending ID order. "
        "Start after_comment_id at 0 after any registry change; when has_more "
        "is true, continue with next_after_comment_id. Does not call YouTube or "
        "run sentiment analysis. Run /sentiment-analyses/analyze-pending afterward."
    ),
    responses={
        404: {"description": "Source not found"},
        409: {"description": "Concurrent mention conflict; retry the same batch"},
    },
)
def backfill_saved_comment_mentions(
    request: MentionBackfillRequest,
    db: Session = Depends(get_db),
):
    try:
        result = backfill_mentions(db, request)
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        diagnostic = getattr(exc.orig, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "uq_mentions_comment_politician":
            raise HTTPException(
                status_code=409,
                detail="Concurrent mention conflict; retry the same batch",
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise


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
