"""订单 / 钱包 / 退款（支付锁定阶段一）。"""

from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class PayOrderIn(ApiModel):
    method: Literal["BALANCE", "MOCK"] = "MOCK"


class TopupIn(ApiModel):
    amount_in_cents: int = Field(ge=100, le=10_000_000)


class WalletGrantIn(ApiModel):
    user_id: int
    amount_in_cents: int = Field(ge=1, le=10_000_000)
    note: str | None = Field(default=None, max_length=255)


class OrderAppointmentBriefOut(ApiModel):
    id: int
    appointment_no: str


class OrderOut(ApiModel):
    order_no: str
    type: Literal["APPOINTMENT", "TOPUP"]
    status: Literal["CREATED", "PAID", "CLOSED", "REFUNDED"]
    amount_in_cents: int
    pay_expire_at: str | None = None
    paid_at: str | None = None
    refunded_at: str | None = None
    appointment: OrderAppointmentBriefOut | None = None
    created_at: str


class WalletOut(ApiModel):
    balance_in_cents: int


class WalletTransactionOut(ApiModel):
    change_in_cents: int
    balance_after: int
    biz_type: str
    note: str | None = None
    created_at: str
