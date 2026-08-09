from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, JSON, SMALLINT

from app.db.base import Base


class Tag(Base):
    """标签体系：擅长领域(FIELD) / 服务人群(AUDIENCE)"""

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("name", "type", name="uq_tags_name_type"),
        Index("idx_tags_enabled", "type", "is_enabled", "sort_order"),
        CheckConstraint(
            "type IN ('FIELD','AUDIENCE')",
            name="chk_tags_type",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False)
    type = Column(String(16), nullable=False, comment="FIELD/AUDIENCE")
    sort_order = Column(INTEGER, nullable=False, server_default=text("0"))
    is_enabled = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class CoachProfile(Base):
    """教练资料与当前审核状态"""

    __tablename__ = "coach_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_coach_profiles_user"),
        Index("idx_coach_profiles_audit_rating", "audit_status", desc("rating")),
        CheckConstraint(
            "audit_status IN ('PENDING','APPROVED','REJECTED')",
            name="chk_coach_profiles_audit",
        ),
        CheckConstraint(
            "rating BETWEEN 0.00 AND 5.00",
            name="chk_coach_profiles_rating",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_coach_profiles_user"),
        nullable=False,
    )
    real_name = Column(String(32), nullable=False, comment="真实姓名（仅后台可见）")
    bio = Column(Text, nullable=True, comment="个人简介")
    training_exp = Column(Text, nullable=True, comment="培训经历")
    service_concept = Column(String(255), nullable=True, comment="服务理念")
    years_of_experience = Column(SMALLINT(unsigned=True), nullable=False, server_default=text("0"))
    credential_urls = Column(JSON, nullable=False, comment="资质证书URL数组（私有）")
    id_card_url = Column(String(512), nullable=True, comment="身份证扫描件（私有）")
    audit_status = Column(String(16), nullable=False, server_default="PENDING", comment="PENDING/APPROVED/REJECTED")
    rating = Column(Numeric(3, 2), nullable=False, server_default=text("0.00"), comment="综合评分(V1.1聚合)")
    review_count = Column(INTEGER(unsigned=True), nullable=False, server_default=text("0"), comment="评价数(V1.1聚合)")
    deleted_at = Column(DATETIME(fsp=3), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class CoachTag(Base):
    """教练-标签关联"""

    __tablename__ = "coach_tags"

    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_coach_tags_coach"),
        primary_key=True,
    )
    tag_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("tags.id", ondelete="CASCADE", name="fk_coach_tags_tag"),
        primary_key=True,
    )
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))

    __table_args__ = (
        Index("idx_coach_tags_tag", "tag_id"),
    )


class CoachAudit(Base):
    """教练审核记录（每次提交/审核一条）"""

    __tablename__ = "coach_audits"
    __table_args__ = (
        Index("idx_coach_audits_coach", "coach_id", "status"),
        Index("idx_coach_audits_status", "status", "submitted_at"),
        CheckConstraint(
            "status IN ('PENDING','APPROVED','REJECTED')",
            name="chk_coach_audits_status",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_coach_audits_coach"),
        nullable=False,
    )
    submit_version = Column(INTEGER(unsigned=True), nullable=False, server_default=text("1"), comment="第几次提交")
    profile_snapshot = Column(JSON, nullable=False, comment="提交时资料快照")
    status = Column(String(16), nullable=False, server_default="PENDING", comment="PENDING/APPROVED/REJECTED")
    remark = Column(String(500), nullable=True, comment="驳回理由/审核备注")
    reviewed_by = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_coach_audits_admin"),
        nullable=True,
        comment="审核管理员",
    )
    reviewed_at = Column(DATETIME(fsp=3), nullable=True)
    submitted_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class Service(Base):
    """服务项目（单次/套餐）"""

    __tablename__ = "services"
    __table_args__ = (
        Index("idx_services_coach", "coach_id", "is_enabled"),
        CheckConstraint(
            "service_type IN ('SINGLE','PACKAGE')",
            name="chk_services_type",
        ),
        CheckConstraint(
            "price_in_cents > 0",
            name="chk_services_price",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_services_coach"),
        nullable=False,
    )
    name = Column(String(64), nullable=False)
    service_type = Column(String(16), nullable=False, comment="SINGLE/PACKAGE")
    duration_min = Column(INTEGER(unsigned=True), nullable=False, server_default=text("60"))
    price_in_cents = Column(INTEGER(unsigned=True), nullable=False, server_default=text("0"), comment="价格(分)")
    description = Column(String(500), nullable=True)
    is_enabled = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class CoachSlot(Base):
    """可预约时段"""

    __tablename__ = "coach_slots"
    __table_args__ = (
        UniqueConstraint("coach_id", "date", "start_time", name="uq_coach_slots"),
        Index("idx_coach_slots_available", "coach_id", "date", "status"),
        CheckConstraint(
            "status IN ('AVAILABLE','BOOKED','OFF')",
            name="chk_coach_slots_status",
        ),
        CheckConstraint(
            "end_time > start_time",
            name="chk_coach_slots_time",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_coach_slots_coach"),
        nullable=False,
    )
    date = Column(Date, nullable=False, comment="日期(Asia/Shanghai)")
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    status = Column(String(16), nullable=False, server_default="AVAILABLE", comment="AVAILABLE/BOOKED/OFF")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class Appointment(Base):
    """预约单（用户—教练服务链路中心表）"""

    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("slot_id", name="uq_appointments_slot"),
        UniqueConstraint("appointment_no", name="uq_appointments_no"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_appointments_idem"),
        Index("idx_appointments_user", "user_id", "status", desc("created_at")),
        Index("idx_appointments_coach", "coach_id", "status", desc("created_at")),
        CheckConstraint(
            "status IN ('PENDING','CONFIRMED','COMPLETED','CANCELLED')",
            name="chk_appointments_status",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    appointment_no = Column(String(32), nullable=False, comment="业务单号")
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_appointments_user"),
        nullable=False,
    )
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="RESTRICT", name="fk_appointments_coach"),
        nullable=False,
    )
    service_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("services.id", ondelete="RESTRICT", name="fk_appointments_service"),
        nullable=False,
    )
    slot_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_slots.id", ondelete="RESTRICT", name="fk_appointments_slot"),
        nullable=False,
    )
    need_desc = Column(String(500), nullable=False, comment="教练式提问收集的需求")
    status = Column(String(16), nullable=False, server_default="PENDING", comment="PENDING/CONFIRMED/COMPLETED/CANCELLED")
    cancel_reason = Column(String(255), nullable=True)
    cancel_by = Column(BIGINT(unsigned=True), nullable=True, comment="取消人(用户或教练ID)")
    idempotency_key = Column(String(36), nullable=True, comment="幂等键")
    completed_at = Column(DATETIME(fsp=3), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class CaseRecord(Base):
    """个案记录"""

    __tablename__ = "case_records"
    __table_args__ = (
        Index("idx_case_records_coach", "coach_id", desc("created_at")),
        Index("idx_case_records_appointment", "appointment_id"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_case_records_coach"),
        nullable=False,
    )
    appointment_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("appointments.id", ondelete="SET NULL", name="fk_case_records_appointment"),
        nullable=True,
    )
    client_nickname = Column(String(32), nullable=True, comment="客户称呼(隐私)")
    key_points = Column(Text, nullable=True, comment="对话核心要点")
    user_gains = Column(Text, nullable=True, comment="用户收获")
    followup_advice = Column(Text, nullable=True, comment="后续跟进建议")
    duration_min = Column(INTEGER(unsigned=True), nullable=False, server_default=text("0"))
    deleted_at = Column(DATETIME(fsp=3), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
