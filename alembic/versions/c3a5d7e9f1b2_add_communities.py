"""add community tables + seed

Revision ID: c3a5d7e9f1b2
Revises: 9b4a2c7d1e6f
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3a5d7e9f1b2"
down_revision: Union[str, Sequence[str], None] = "9b4a2c7d1e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "communities",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("cover_url", sa.String(length=512), nullable=True),
        sa.Column("coach_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("member_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_members", sa.Integer(), server_default=sa.text("500"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("created_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.Column("updated_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["coach_id"], ["coach_profiles.id"], name="fk_communities_coach", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_communities_name"),
    )
    op.create_index("idx_communities_status", "communities", ["status", "created_at"], unique=False)

    op.create_table(
        "community_members",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("community_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="MEMBER", nullable=False),
        sa.Column("joined_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["community_id"], ["communities.id"], name="fk_community_members_community", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_community_members_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("community_id", "user_id", name="uq_community_members"),
    )
    op.create_index("idx_community_members_user", "community_members", ["user_id"], unique=False)

    op.create_table(
        "community_posts",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("community_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(length=512), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.text("0"), nullable=False),
        sa.Column("like_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.Column("updated_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["community_id"], ["communities.id"], name="fk_community_posts_community", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_community_posts_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_community_posts_feed", "community_posts", ["community_id", "is_pinned", sa.text("created_at DESC")], unique=False)

    op.create_table(
        "community_comments",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["community_posts.id"], name="fk_community_comments_post", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_community_comments_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_community_comments_post", "community_comments", ["post_id", sa.text("created_at DESC")], unique=False)

    op.create_table(
        "community_likes",
        sa.Column("id", sa.dialects.mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("user_id", sa.dialects.mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("created_at", sa.dialects.mysql.DATETIME(fsp=3), server_default=sa.text("CURRENT_TIMESTAMP(3)"), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["community_posts.id"], name="fk_community_likes_post", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_community_likes_user", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "user_id", name="uq_community_likes"),
    )

    # ---------- 种子社群（自由加入，带队教练可后续创建） ----------
    communities = sa.table(
        "communities",
        sa.column("id", sa.BigInteger),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("member_count", sa.Integer),
        sa.column("max_members", sa.Integer),
        sa.column("status", sa.String),
    )
    op.bulk_insert(communities, [
        {
            "id": 1,
            "name": "高考家长互助群",
            "description": "备考路上，家长也需要被支持。在这里分享经验、互相陪伴，用资源视角看待考前阶段。",
            "member_count": 0,
            "max_members": 500,
            "status": "ACTIVE",
        },
        {
            "id": 2,
            "name": "职场成长群",
            "description": "职场压力、职业方向、行动复盘，一起聊聊怎么把困惑变成下一步。",
            "member_count": 0,
            "max_members": 500,
            "status": "ACTIVE",
        },
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("community_likes")
    op.drop_index("idx_community_comments_post", table_name="community_comments")
    op.drop_table("community_comments")
    op.drop_index("idx_community_posts_feed", table_name="community_posts")
    op.drop_table("community_posts")
    op.drop_index("idx_community_members_user", table_name="community_members")
    op.drop_table("community_members")
    op.drop_index("idx_communities_status", table_name="communities")
    op.drop_table("communities")
