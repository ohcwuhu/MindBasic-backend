from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON

from app.db.base import Base


class CoachingTemplate(Base):
    """自我教练模板"""

    __tablename__ = "coaching_templates"
    __table_args__ = (
        Index("idx_coaching_templates_enabled", "is_enabled", "sort_order"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    scene = Column(String(64), nullable=False)
    description = Column(String(255), nullable=True)
    is_enabled = Column(Boolean, nullable=False, server_default=text("1"))
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class TemplateStep(Base):
    """模板步骤（现状→理想→资源→行动）"""

    __tablename__ = "template_steps"
    __table_args__ = (
        UniqueConstraint("template_id", "step_key", name="uq_template_steps"),
        Index("idx_template_steps_order", "template_id", "sort_order"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    template_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coaching_templates.id", ondelete="CASCADE", name="fk_template_steps_template"),
        nullable=False,
    )
    step_key = Column(String(32), nullable=False, comment="STATUS/IDEAL/RESOURCES/ACTION")
    step_name = Column(String(32), nullable=False)
    question = Column(Text, nullable=False, comment="内置教练式问句")
    placeholder = Column(String(255), nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class SelfCoachingRecord(Base):
    """自我教练记录"""

    __tablename__ = "self_coaching_records"
    __table_args__ = (
        Index("idx_self_coaching_user", "user_id", desc("created_at")),
        CheckConstraint(
            "status IN ('DRAFT','COMPLETED')",
            name="chk_self_coaching_status",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_self_coaching_user"),
        nullable=False,
    )
    template_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coaching_templates.id", ondelete="RESTRICT", name="fk_self_coaching_template"),
        nullable=False,
    )
    answers = Column(JSON, nullable=False, comment="{stepKey: 回答文本}")
    action_card = Column(JSON, nullable=True, comment="成长行动卡")
    status = Column(String(16), nullable=False, server_default="DRAFT", comment="DRAFT/COMPLETED")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class EmotionJournal(Base):
    """情绪日记"""

    __tablename__ = "emotion_journals"
    __table_args__ = (
        Index("idx_emotion_journals_user", "user_id", desc("created_at")),
        CheckConstraint(
            "mood_type IN ('CALM','HAPPY','ANXIOUS','DOWN','IRRITATED','OTHER')",
            name="chk_emotion_journals_mood",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_emotion_journals_user"),
        nullable=False,
    )
    mood_type = Column(String(16), nullable=False, comment="CALM/HAPPY/ANXIOUS/DOWN/IRRITATED/OTHER")
    content = Column(String(500), nullable=False, comment="一句话描述")
    feedback = Column(String(500), nullable=True, comment="话术快照")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class EmotionFeedbackLib(Base):
    """情绪反馈话术库（本期替代 AI 反馈）"""

    __tablename__ = "emotion_feedback_lib"
    __table_args__ = (
        Index("idx_feedback_lib", "mood_type", "is_enabled", "sort_order"),
        CheckConstraint(
            "mood_type IN ('CALM','HAPPY','ANXIOUS','DOWN','IRRITATED','OTHER')",
            name="chk_feedback_lib_mood",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    mood_type = Column(String(16), nullable=False)
    content = Column(String(500), nullable=False, comment="资源导向鼓励话术")
    is_enabled = Column(Boolean, nullable=False, server_default=text("1"))
    sort_order = Column(Integer, nullable=False, server_default=text("0"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
