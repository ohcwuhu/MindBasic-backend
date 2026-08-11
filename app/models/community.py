"""主题社群：社群、成员、帖子、评论、点赞。"""

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

from app.db.base import Base


class Community(Base):
    """主题社群（可由已审核教练带队创建）。"""

    __tablename__ = "communities"
    __table_args__ = (
        Index("idx_communities_status", "status", "created_at"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, unique=True)
    description = Column(String(500), nullable=False)
    cover_url = Column(String(512), nullable=True)
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="SET NULL", name="fk_communities_coach"),
        nullable=True,
        comment="带队教练（可空）",
    )
    member_count = Column(Integer, nullable=False, server_default=text("0"), comment="成员数（冗余计数）")
    max_members = Column(Integer, nullable=False, server_default=text("500"))
    status = Column(String(16), nullable=False, server_default="ACTIVE", comment="ACTIVE/DISABLED")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class CommunityMember(Base):
    """社群成员关系。"""

    __tablename__ = "community_members"
    __table_args__ = (
        UniqueConstraint("community_id", "user_id", name="uq_community_members"),
        Index("idx_community_members_user", "user_id"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    community_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("communities.id", ondelete="CASCADE", name="fk_community_members_community"),
        nullable=False,
    )
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_community_members_user"),
        nullable=False,
    )
    role = Column(String(16), nullable=False, server_default="MEMBER", comment="OWNER/MODERATOR/MEMBER")
    joined_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class CommunityPost(Base):
    """社群帖子。"""

    __tablename__ = "community_posts"
    __table_args__ = (
        Index("idx_community_posts_feed", "community_id", "is_pinned", desc("created_at")),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    community_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("communities.id", ondelete="CASCADE", name="fk_community_posts_community"),
        nullable=False,
    )
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_community_posts_user"),
        nullable=False,
    )
    content = Column(Text, nullable=False)
    image_url = Column(String(512), nullable=True)
    is_pinned = Column(Boolean, nullable=False, server_default=text("0"))
    like_count = Column(Integer, nullable=False, server_default=text("0"), comment="点赞数（冗余计数）")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class CommunityComment(Base):
    """帖子评论。"""

    __tablename__ = "community_comments"
    __table_args__ = (
        Index("idx_community_comments_post", "post_id", desc("created_at")),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    post_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("community_posts.id", ondelete="CASCADE", name="fk_community_comments_post"),
        nullable=False,
    )
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_community_comments_user"),
        nullable=False,
    )
    content = Column(String(500), nullable=False)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class CommunityLike(Base):
    """帖子点赞（幂等去重）。"""

    __tablename__ = "community_likes"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_community_likes"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    post_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("community_posts.id", ondelete="CASCADE", name="fk_community_likes_post"),
        nullable=False,
    )
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_community_likes_user"),
        nullable=False,
    )
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
