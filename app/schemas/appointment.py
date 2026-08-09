from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class AppointmentCreateIn(ApiModel):
    coach_id: int
    service_id: int
    slot_id: int
    need_desc: str = Field(min_length=1, max_length=500)


class SlotBriefOut(ApiModel):
    id: int
    date: str
    start_time: str
    end_time: str


class ServiceBriefOut(ApiModel):
    id: int
    name: str
    service_type: Literal["SINGLE", "PACKAGE"]
    price_in_cents: int


class UserBriefOut(ApiModel):
    id: int
    nickname: str
    phone: str


class CoachBriefOut(ApiModel):
    id: int
    nickname: str
    avatar_url: str | None = None


class AppointmentOut(ApiModel):
    id: int
    appointment_no: str
    coach: CoachBriefOut
    service: ServiceBriefOut
    slot: SlotBriefOut
    need_desc: str
    status: Literal["PENDING", "CONFIRMED", "COMPLETED", "CANCELLED"]
    cancel_reason: str | None = None
    can_cancel: bool
    created_at: str


class CoachAppointmentOut(ApiModel):
    id: int
    appointment_no: str
    user: UserBriefOut
    service: ServiceBriefOut
    slot: SlotBriefOut
    need_desc: str
    status: Literal["PENDING", "CONFIRMED", "COMPLETED", "CANCELLED"]
    cancel_reason: str | None = None
    created_at: str
    completed_at: str | None = None


class AppointmentStatusOut(ApiModel):
    id: int
    status: Literal["PENDING", "CONFIRMED", "COMPLETED", "CANCELLED"]
    cancel_reason: str | None = None
    completed_at: str | None = None


class CancelAppointmentIn(ApiModel):
    cancel_reason: str = Field(min_length=1, max_length=255)
