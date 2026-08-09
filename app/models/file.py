"""上传文件元数据。"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME

from app.db.base import Base


class FileUpload(Base):
    """上传文件记录：归属用户、用途与私有标记。"""

    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_files_file_id"),
        Index("idx_files_user", "user_id"),
        Index("idx_files_private", "is_private"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    file_id = Column(String(64), nullable=False, comment="存储文件名（uuid.ext）")
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_files_user"),
        nullable=False,
    )
    usage = Column(String(16), nullable=False, comment="general/credential/idcard")
    is_private = Column(Boolean, nullable=False, server_default=text("0"))
    original_name = Column(String(255), nullable=False)
    content_type = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
