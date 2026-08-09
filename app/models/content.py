from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, JSON, MEDIUMTEXT

from app.db.base import Base


class ArticleCategory(Base):
    """文章分类"""

    __tablename__ = "article_categories"
    __table_args__ = (
        UniqueConstraint("name", name="uq_article_categories_name"),
        Index("idx_article_categories_enabled", "is_enabled", "sort_order"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    sort_order = Column(INTEGER, nullable=False, server_default=text("0"))
    is_enabled = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class Article(Base):
    """科普文章"""

    __tablename__ = "articles"
    __table_args__ = (
        Index("idx_articles_category", "category_id", "is_pinned", "status"),
        Index("idx_articles_published", "status", desc("published_at")),
        CheckConstraint(
            "status IN ('PUBLISHED','DRAFT','OFFLINE')",
            name="chk_articles_status",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    title = Column(String(128), nullable=False)
    summary = Column(String(255), nullable=True)
    content = Column(MEDIUMTEXT, nullable=False, comment="富文本")
    cover_url = Column(String(512), nullable=True)
    category_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("article_categories.id", ondelete="SET NULL", name="fk_articles_category"),
        nullable=True,
    )
    is_pinned = Column(Boolean, nullable=False, server_default=text("0"))
    status = Column(String(16), nullable=False, server_default="PUBLISHED", comment="PUBLISHED/DRAFT/OFFLINE")
    view_count = Column(INTEGER(unsigned=True), nullable=False, server_default=text("0"))
    published_at = Column(DATETIME(fsp=3), nullable=True)
    deleted_at = Column(DATETIME(fsp=3), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class ArticleFavorite(Base):
    """文章收藏"""

    __tablename__ = "article_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uq_article_favorites"),
        Index("idx_article_favorites_article", "article_id"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_article_favorites_user"),
        nullable=False,
    )
    article_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("articles.id", ondelete="CASCADE", name="fk_article_favorites_article"),
        nullable=False,
    )
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class Banner(Base):
    """首页轮播图"""

    __tablename__ = "banners"
    __table_args__ = (
        Index("idx_banners_enabled", "is_enabled", "sort_order"),
        CheckConstraint(
            "link_type IN ('NONE','ARTICLE','ACTIVITY','URL')",
            name="chk_banners_link",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    title = Column(String(64), nullable=False)
    image_url = Column(String(512), nullable=False)
    link_type = Column(String(16), nullable=False, server_default="NONE", comment="NONE/ARTICLE/ACTIVITY/URL")
    link_value = Column(String(512), nullable=True, comment="文章ID/活动标识/外链URL")
    sort_order = Column(INTEGER, nullable=False, server_default=text("0"))
    is_enabled = Column(Boolean, nullable=False, server_default=text("1"))
    start_at = Column(DATETIME(fsp=3), nullable=True, comment="生效时间(可选)")
    end_at = Column(DATETIME(fsp=3), nullable=True, comment="失效时间(可选)")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class SystemConfig(Base):
    """系统配置（键值 + JSON）"""

    __tablename__ = "system_configs"
    __table_args__ = (
        UniqueConstraint("config_key", name="uq_system_configs_key"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    config_key = Column(String(64), nullable=False)
    config_value = Column(JSON, nullable=False)
    description = Column(String(255), nullable=True)
    updated_by = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_system_configs_admin"),
        nullable=True,
    )
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
