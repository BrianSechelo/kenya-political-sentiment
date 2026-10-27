"""Stage missing mentions for a bounded page of already stored comments."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Comment, Mention, Politician, PoliticianAlias, Source
from app.schemas.mention import MentionBackfillRequest, MentionBackfillResponse
from app.services.mention_detection import match_politician_ids


def backfill_mentions(
    db: Session, request: MentionBackfillRequest
) -> MentionBackfillResponse:
    """Use the existing lexical matcher; the caller owns commit/rollback.

    Scan ALL comments in the page, including ones already mentioning another
    politician. Adding a new alias can create a second mention on those rows.
    Never remove old mentions or rewrite predictions when the registry changes.
    """
    if db.get(Source, request.source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found")

    page = (
        db.query(Comment)
        .filter(
            Comment.source_id == request.source_id,
            Comment.comment_id > request.after_comment_id,
        )
        .order_by(Comment.comment_id)
        .limit(request.limit + 1)
        .all()
    )
    has_more = len(page) > request.limit
    comments = page[:request.limit]
    if not comments:
        return MentionBackfillResponse(
            source_id=request.source_id, processed_count=0,
            comments_with_new_mentions=0, mentions_created=0,
            next_after_comment_id=None, has_more=False,
        )

    # Load one registry snapshot and all existing page links, rather than
    # fetching politicians, aliases, and mentions separately for every comment.
    politicians = (
        db.query(Politician.politician_id, Politician.full_name)
        .filter(Politician.is_active.is_(True))
        .all()
    )
    aliases = (
        db.query(PoliticianAlias.politician_id, PoliticianAlias.alias)
        .join(Politician, Politician.politician_id == PoliticianAlias.politician_id)
        .filter(Politician.is_active.is_(True))
        .all()
    )
    names = [*politicians, *aliases]
    existing = {
        (row.comment_id, row.politician_id)
        for row in db.query(Mention.comment_id, Mention.politician_id)
        .filter(Mention.comment_id.in_([comment.comment_id for comment in comments]))
        .all()
    }
    created = []
    changed_comments = set()
    for comment in comments:
        for politician_id in match_politician_ids(comment.text, names):
            pair = (comment.comment_id, politician_id)
            if pair not in existing:
                created.append(Mention(
                    comment_id=comment.comment_id,
                    politician_id=politician_id,
                ))
                existing.add(pair)
                changed_comments.add(comment.comment_id)
    db.add_all(created)
    return MentionBackfillResponse(
        source_id=request.source_id,
        processed_count=len(comments),
        comments_with_new_mentions=len(changed_comments),
        mentions_created=len(created),
        next_after_comment_id=comments[-1].comment_id if has_more else None,
        has_more=has_more,
    )
