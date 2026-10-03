"""多模态分析留痕：为模型评测、效果追溯与风险复核提供结构化数据。"""

from sqlalchemy import Boolean, Column, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, JSON

from app.db.base import Base


class MultimodalAnalysisRecord(Base):
    """单次多模态分析的输入、输出、权重与耗时快照。

    写入本表的目的是让"识别结果"从日志变成可统计的数据资产：
    消融实验、指标报表、风险复核、时延分析都依赖这张表。
    """

    __tablename__ = "multimodal_analysis_records"
    __table_args__ = (
        Index("idx_mmar_user_time", "user_id", "created_at"),
        Index("idx_mmar_source_time", "source", "created_at"),
        Index("idx_mmar_risk_time", "risk_level", "created_at"),
        Index("idx_mmar_stage_time", "coach_stage", "created_at"),
        Index("idx_mmar_conversation", "conversation_id"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_mmar_user"),
        nullable=True,
        comment="触发分析的用户（匿名或已注销时为空）",
    )
    source = Column(
        String(24),
        nullable=False,
        comment="分析入口：HTTP_ANALYZE / VIDEO_CALL",
    )
    session_id = Column(String(64), nullable=True, comment="SocketIO sid（实时管线）")

    asr_text = Column(Text, nullable=True, comment="ASR 转写文本")
    asr_emotion = Column(String(16), nullable=True, comment="SenseVoice 情绪辅助信号")

    text_emotion = Column(String(16), nullable=True)
    text_confidence = Column(Float, nullable=True)
    voice_emotion = Column(String(16), nullable=True)
    voice_confidence = Column(Float, nullable=True)
    facial_emotion = Column(String(16), nullable=True)
    facial_confidence = Column(Float, nullable=True)
    facial_frames = Column(Integer, nullable=True, comment="参与聚合的面部帧数")

    fusion_emotion = Column(String(16), nullable=True)
    fusion_confidence = Column(Float, nullable=True)
    weights = Column(JSON, nullable=True, comment="{text, voice, facial} 实际权重")
    weight_adjustments = Column(JSON, nullable=True, comment="权重调整原因列表")
    calibration = Column(JSON, nullable=True, comment="置信度校准元信息（方法/温度/来源）")
    conflict = Column(JSON, nullable=True, comment="线索冲突度量（JS 散度、是否需澄清）")

    risk_level = Column(String(8), nullable=True, comment="NONE/LOW/MEDIUM/HIGH")
    risk_score = Column(Integer, nullable=True, comment="0-100 风险分")
    risk_reasons = Column(JSON, nullable=True, comment="风险判定依据")
    dify_risk_level = Column(
        String(8),
        nullable=True,
        comment="Dify 工作流自判的风险等级（high/medium/low），用于两侧一致性统计",
    )
    fallback_risk_level = Column(
        String(8),
        nullable=True,
        comment=(
            "兜底模型自判的风险等级（low/medium/high）：Dify 不可用时的第二意见，"
            "与 dify_risk_level 分列存放以免口径混用"
        ),
    )

    coach_stage = Column(
        String(24),
        nullable=True,
        comment="该轮五阶段判定：opening/exploration/goal_setting/action_planning/closing",
    )
    goal_clear = Column(Boolean, nullable=True, comment="目标是否已经清楚")
    action_ready = Column(Boolean, nullable=True, comment="是否已形成可执行行动")
    should_summarize = Column(Boolean, nullable=True, comment="是否满足收束条件")
    conversation_id = Column(
        BIGINT(unsigned=True),
        ForeignKey(
            "ai_conversations.id",
            ondelete="SET NULL",
            name="fk_mmar_conversation",
        ),
        nullable=True,
        comment="所属 AI 教练会话（实时管线写入）",
    )

    timings = Column(JSON, nullable=True, comment="各阶段耗时（秒）")
    status = Column(String(16), nullable=False, server_default="ok", comment="ok/partial_success/failed")
    created_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    )
