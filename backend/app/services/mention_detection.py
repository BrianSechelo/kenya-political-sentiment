"""Conservative name/alias matching; callers own the database transaction."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models import Comment, Mention, Politician, PoliticianAlias


def normalize_name_text(value: str) -> str:
    """Normalize case, Unicode width, punctuation and whitespace into words."""
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", value, flags=re.UNICODE))


def match_politician_ids(
    text: str,
    names: Iterable[tuple[int, str]],
) -> list[int]:
    """Match complete word sequences, ignoring names shared by politicians.

    Matching a registered alias is a lexical rule, not a probability or a
    guarantee about the author's intended target. No fuzzy matching is used.
    """
    normalized_text = normalize_name_text(text)
    if not normalized_text:
        return []

    owners: dict[str, set[int]] = defaultdict(set)
    for politician_id, name in names:
        normalized_name = normalize_name_text(name)
        if normalized_name:
            owners[normalized_name].add(politician_id)

    padded_text = f" {normalized_text} "
    matches: set[int] = set()
    for name, politician_ids in owners.items():
        if len(politician_ids) == 1 and f" {name} " in padded_text:
            matches.update(politician_ids)
    return sorted(matches)


def create_mentions_for_comment(db: Session, comment: Comment) -> list[Mention]:
    """Stage missing mentions for a flushed comment; never commit here.

    Only active politicians are monitored. A politician is added once even if
    their full name and several aliases occur. Shared normalized aliases are
    ignored, although another unique name in the comment can still match.
    Existing mentions are retained. The caller commits or rolls back the whole
    operation. relevance_score remains NULL: lexical matching is uncalibrated.
    """
    if comment.comment_id is None:
        raise ValueError("Flush the comment before detecting mentions")

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
    matched_ids = match_politician_ids(comment.text, [*politicians, *aliases])
    if not matched_ids:
        return []

    # Flush staged mentions too, so repeat calls in this transaction are safe
    # even if the application's SessionLocal has autoflush=False.
    db.flush()
    existing_ids = {
        row[0]
        for row in db.query(Mention.politician_id)
        .filter(Mention.comment_id == comment.comment_id)
        .all()
    }
    created = [
        Mention(comment_id=comment.comment_id, politician_id=politician_id)
        for politician_id in matched_ids
        if politician_id not in existing_ids
    ]
    db.add_all(created)
    return created
