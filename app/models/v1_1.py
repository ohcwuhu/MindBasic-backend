"""V1.1 预留表：本期无业务代码，schema 随初始迁移创建。"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER, JSON, SMALLINT

from app.db.base import Base


class Review(Base):
    """用户评价（V1.1）"""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("appointment_id", name="uq_reviews_appointment"),
        Index("idx_reviews_coach", "coach_id", desc("created_at")),
        CheckConstraint(
            "rating BETWEEN 1 AND 5",
            name="chk_reviews_rating",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    appointment_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("appointments.id", ondelete="CASCADE", name="fk_reviews_appointment"),
        nullable=False,
    )
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_reviews_coach"),
        nullable=False,
    )
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_reviews_user"),
        nullable=False,
    )
    rating = Column(SMALLINT(unsigned=True), nullable=False, comment="1-5")
    content = Column(String(500), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))


class Order(Base):
    """订单（V1.1）"""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("order_no", name="uq_orders_no"),
        Index("idx_orders_user", "user_id", desc("created_at")),
        Index("idx_orders_coach", "coach_id", desc("created_at")),
        CheckConstraint(
            "status IN ('CREATED','PAID','CLOSED','REFUNDED')",
            name="chk_orders_status",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    order_no = Column(String(32), nullable=False)
    user_id = Column(BIGINT(unsigned=True), nullable=False)
    coach_id = Column(BIGINT(unsigned=True), nullable=False)
    service_id = Column(BIGINT(unsigned=True), nullable=False)
    amount_in_cents = Column(INTEGER(unsigned=True), nullable=False)
    platform_fee_in_cents = Column(INTEGER(unsigned=True), nullable=False, server_default=text("0"))
    status = Column(String(16), nullable=False, server_default="CREATED", comment="CREATED/PAID/CLOSED/REFUNDED")
    paid_at = Column(DATETIME(fsp=3), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )


class Payment(Base):
    """支付流水（V1.1）"""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_payments_transaction"),
        Index("idx_payments_order", "order_id"),
        CheckConstraint(
            "status IN ('PENDING','SUCCESS','FAILED','REFUNDED')",
            name="chk_payments_status",
        ),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    order_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("orders.id", ondelete="RESTRICT", name="fk_payments_order"),
        nullable=False,
    )
    pay_channel = Column(String(16), nullable=False, server_default="WECHAT")
    transaction_id = Column(String(64), nullable=True, comment="渠道交易号")
    status = Column(String(16), nullable=False, comment="PENDING/SUCCESS/FAILED/REFUNDED")
    raw_notify = Column(JSON, nullable=True, comment="渠道回调原文")
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    paid_at = Column(DATETIME(fsp=3), nullable=True)


class ClientRelation(Base):
    """教练客户档案（V1.1）"""

    __tablename__ = "client_relations"
    __table_args__ = (
        UniqueConstraint("coach_id", "user_id", name="uq_client_relations"),
        Index("idx_client_relations_user", "user_id"),
    )

    id = Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)
    coach_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("coach_profiles.id", ondelete="CASCADE", name="fk_client_relations_coach"),
        nullable=False,
    )
    user_id = Column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_client_relations_user"),
        nullable=False,
    )
    last_appointment_at = Column(DATETIME(fsp=3), nullable=True)
    remark = Column(String(255), nullable=True)
    created_at = Column(DATETIME(fsp=3), nullable=False, server_default=text("CURRENT_TIMESTAMP(3)"))
    updated_at = Column(
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
        onupdate=text("CURRENT_TIMESTAMP(3)"),
    )
