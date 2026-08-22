from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
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


class CheckIn(Base):
    """每日小行动打卡。"""

    __tablename__ = "check_ins"
    __table_args__ = (
        UniqueConstraint("user_id", "check_date", name="uq_check_ins_user_date"),
        Index("idx_check_ins_date", "check_date"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_check_ins_user"),
        nullable=False,
    )
    check_date = Column(Date, nullable=False)
    content = Column(String(200), nullable=True, comment="今日小行动")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class Badge(Base):
    """勋章定义。"""

    __tablename__ = "badges"
    __table_args__ = (
        UniqueConstraint("key", name="uq_badges_key"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    key = Column(String(32), nullable=False)
    name = Column(String(32), nullable=False)
    description = Column(String(200), nullable=False)
    icon = Column(String(64), nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text("0"))


class UserBadge(Base):
    """用户已获得勋章。"""

    __tablename__ = "user_badges"
    __table_args__ = (
        UniqueConstraint("user_id", "badge_id", name="uq_user_badges"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_badges_user"),
        nullable=False,
    )
    badge_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("badges.id", ondelete="CASCADE", name="fk_user_badges_badge"),
        nullable=False,
    )
    earned_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


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
        UniqueConstraint("source_conversation_id", name="uq_emotion_journals_conv"),
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
    source = Column(String(16), nullable=False, server_default="MANUAL", comment="MANUAL/SELF_COACHING")
    source_conversation_id = Column(BIGINT(unsigned=True), nullable=True, comment="来源 AI 对话 ID")
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


class GrowthAssessmentTemplate(Base):
    """成长测评量表模板（由教练代表评审维护，版本化）。"""

    __tablename__ = "growth_assessment_templates"
    __table_args__ = (
        Index("idx_growth_templates_enabled", "is_enabled"),
        UniqueConstraint("name", "version", name="uq_growth_template_version"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    description = Column(String(255), nullable=True)
    version = Column(Integer, nullable=False, server_default=text("1"))
    is_enabled = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class GrowthAssessmentQuestion(Base):
    """测评题目（每维度 3 题，5 点 Likert）。"""

    __tablename__ = "growth_assessment_questions"
    __table_args__ = (
        Index("idx_growth_questions_template", "template_id", "sort_order"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    template_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("growth_assessment_templates.id", ondelete="CASCADE", name="fk_growth_questions_template"),
        nullable=False,
    )
    dimension_key = Column(String(32), nullable=False, comment="SELF_AWARENESS/RESOURCE_USE/GOAL_CLARITY/ACTION/EMOTION_REGULATION")
    dimension_name = Column(String(32), nullable=False)
    question = Column(Text, nullable=False)
    options = Column(JSON, nullable=False, comment="[{value,label}]")
    sort_order = Column(Integer, nullable=False, server_default=text("0"))


class GrowthAssessmentResult(Base):
    """测评结果：答案快照 + 维度得分 + 个性化报告。"""

    __tablename__ = "growth_assessment_results"
    __table_args__ = (
        Index("idx_growth_results_user", "user_id", desc("created_at")),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_growth_results_user"),
        nullable=False,
    )
    template_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("growth_assessment_templates.id", ondelete="RESTRICT", name="fk_growth_results_template"),
        nullable=False,
    )
    answers = Column(JSON, nullable=False, comment="{questionId: score}")
    scores = Column(JSON, nullable=False, comment="[{dimensionKey,score,level}]")
    report = Column(JSON, nullable=False, comment="个性化报告与推荐")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
