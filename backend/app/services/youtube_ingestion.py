"""Save a bounded YouTube batch and its detected mentions in one transaction."""

from sqlalchemy.orm import Session

from app.models import Comment, Source
from app.schemas.youtube import YouTubeIngestRequest, YouTubeIngestResponse
from app.services.mention_detection import create_mentions_for_comment
from app.services.youtube_client import YouTubeClient, extract_video_id


def ingest_youtube_video(
    db: Session, client: YouTubeClient, request: YouTubeIngestRequest,
) -> YouTubeIngestResponse:
    video_id = extract_video_id(request.video_url)
    # Complete network calls before starting database work. If a later upstream
    # page fails, we do not silently save a partial batch or hold DB locks.
    video = client.fetch_video(video_id)
    batch = client.fetch_comments(video_id, request.max_comments, request.page_token)
    try:
        source = db.query(Source).filter(
            Source.platform == "youtube", Source.platform_source_id == video_id,
        ).first()
        source_created = source is None
        if source is None:
            source = Source(platform="youtube", platform_source_id=video_id)
            db.add(source)
        source.title = video.title
        source.channel_name = video.channel_name
        source.source_url = f"https://www.youtube.com/watch?v={video_id}"
        source.published_at = video.published_at
        source.raw_data = {
            **(source.raw_data or {}),
            "youtube": {"video_id": video_id, "channel_id": video.channel_id},
        }
        db.flush()
        source_id = source.source_id

        identifiers = [item.platform_comment_id for item in batch.comments]
        existing_ids = {
            row[0] for row in db.query(Comment.platform_comment_id).filter(
                Comment.source_id == source_id,
                Comment.platform_comment_id.in_(identifiers),
            ).all()
        } if identifiers else set()
        created_count = existing_count = mentions_created = 0
        for item in batch.comments:
            if item.platform_comment_id in existing_ids:
                existing_count += 1
                continue
            comment = Comment(
                source_id=source_id,
                platform_comment_id=item.platform_comment_id,
                text=item.text,
                like_count=item.like_count,
                published_at=item.published_at,
                raw_data={
                    "origin": "youtube-data-api-v3",
                    "video_id": video_id,
                    "comment_scope": "top_level",
                    "text_format": "plainText",
                    "updated_at": item.updated_at,
                },
            )
            db.add(comment)
            db.flush()
            mentions_created += len(create_mentions_for_comment(db, comment))
            existing_ids.add(item.platform_comment_id)
            created_count += 1
        result = YouTubeIngestResponse(
            video_id=video_id, source_id=source_id, source_created=source_created,
            video_title=video.title, fetched_count=batch.fetched_count,
            created_count=created_count, existing_count=existing_count,
            skipped_count=batch.skipped_count, mentions_created=mentions_created,
            pages_fetched=batch.pages_fetched, next_page_token=batch.next_page_token,
            has_more=batch.next_page_token is not None,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
