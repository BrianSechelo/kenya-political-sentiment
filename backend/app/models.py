from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Numeric,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class Politician(Base):
    __tablename__ = "politicians"

    politician_id = Column(Integer, primary_key=True)
    full_name = Column(String(150), nullable=False)
    slug = Column(String(150), nullable=False, unique=True, index=True)
    party = Column(String(150), nullable=True)
    current_position = Column(String(150), nullable=True)
    profile_image_url = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

class PoliticianAlias(Base):
    __tablename__ = "politician_aliases"

    __table_args__ = (
    UniqueConstraint(
        "politician_id",
        "alias",
        name="uq_politician_aliases_politician_id_alias",
    ),
)

    alias_id = Column(Integer, primary_key=True)
    politician_id = Column(
        Integer,
        ForeignKey("politicians.politician_id"),
        nullable=False,
        index=True,
    )
    alias = Column(String(150), nullable=False)
    alias_type = Column(String(50), nullable=False)
    created_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

class Source(Base):
    __tablename__ = "sources"

    source_id = Column(Integer, primary_key=True)

    platform = Column(
        String(50),
        nullable=False,
    )

    platform_source_id = Column(
        String(255),
        nullable=False,
    )

    title = Column(
        Text,
        nullable=False,
    )

    channel_name = Column(
        String(255),
        nullable=True,
    )

    source_url = Column(
        Text,
        nullable=False,
    )

    published_at = Column(
        DateTime,
        nullable=True,
    )

    discovered_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    raw_data = Column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "platform_source_id",
            name="uq_sources_platform_source_id",
        ),
    )

class SourcePolitician(Base):
    __tablename__ = "source_politicians"

    source_id = Column(
        Integer,
        ForeignKey("sources.source_id"),
        primary_key=True,
    )

    politician_id = Column(
        Integer,
        ForeignKey("politicians.politician_id"),
        primary_key=True,
    )

    added_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

class Comment(Base):
    __tablename__ = "comments"

    comment_id = Column(
        Integer,
        primary_key=True,
    )

    source_id = Column(
        Integer,
        ForeignKey("sources.source_id"),
        nullable=False,
        index=True,
    )

    platform_comment_id = Column(
        String(255),
        nullable=False,
    )

    parent_comment_id = Column(
        String(255),
        nullable=True,
    )

    text = Column(
        Text,
        nullable=False,
    )

    author_hash = Column(
        String(255),
        nullable=True,
    )

    like_count = Column(
        Integer,
        nullable=True,
    )

    published_at = Column(
        DateTime,
        nullable=True,
    )

    collected_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    raw_data = Column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "platform_comment_id",
            name="uq_comments_source_platform_comment",
        ),
    )

class Mention(Base):
    __tablename__ = "mentions"

    mention_id = Column(
        Integer,
        primary_key=True,
    )

    comment_id = Column(
        Integer,
        ForeignKey("comments.comment_id"),
        nullable=False,
        index=True,
    )

    politician_id = Column(
        Integer,
        ForeignKey("politicians.politician_id"),
        nullable=False,
        index=True,
    )

    relevance_score = Column(
        Numeric(5, 4),
        nullable=True,
    )

    detected_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "comment_id",
            "politician_id",
            name="uq_mentions_comment_politician",
        ),
    )

class SentimentAnalysis(Base):
    __tablename__ = "sentiment_analyses"

    analysis_id = Column(
        Integer,
        primary_key=True,
    )

    mention_id = Column(
        Integer,
        ForeignKey("mentions.mention_id"),
        nullable=False,
        index=True,
    )

    model_version = Column(
        String(100),
        nullable=False,
    )

    sentiment_label = Column(
        String(20),
        nullable=False,
    )

    confidence_score = Column(
        Numeric(5, 4),
        nullable=True,
    )

    analyzed_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "mention_id",
            "model_version",
            name="uq_sentiment_analyses_mention_model",
        ),
    )