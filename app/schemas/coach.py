from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class ServiceIn(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    service_type: Literal["SINGLE", "PACKAGE"]
    duration_min: int = Field(ge=15, le=480)
    price_in_cents: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=500)


class ServicePatchIn(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    service_type: Literal["SINGLE", "PACKAGE"] | None = None
    duration_min: int | None = Field(default=None, ge=15, le=480)
    price_in_cents: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=500)
    is_enabled: bool | None = None


class SlotIn(ApiModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")


class SlotBatchIn(ApiModel):
    slots: list[SlotIn] = Field(default_factory=list, min_length=1)


class SlotOut(ApiModel):
    id: int
    coach_id: int
    date: str
    start_time: str
    end_time: str
    status: Literal["AVAILABLE", "BOOKED", "OFF"]


class CoachProfileIn(ApiModel):
    real_name: str = Field(min_length=1, max_length=32)
    bio: str | None = Field(default=None, max_length=2000)
    training_exp: str | None = Field(default=None, max_length=4000)
    service_concept: str | None = Field(default=None, max_length=255)
    years_of_experience: int = Field(default=0, ge=0, le=60)
    credential_urls: list[str] = Field(default_factory=list)
    id_card_url: str | None = Field(default=None, max_length=512)
    tag_ids: list[int] = Field(default_factory=list)
    services: list[ServiceIn] = Field(default_factory=list)


class CoachProfilePatchIn(ApiModel):
    real_name: str | None = Field(default=None, min_length=1, max_length=32)
    bio: str | None = Field(default=None, max_length=2000)
    training_exp: str | None = Field(default=None, max_length=4000)
    service_concept: str | None = Field(default=None, max_length=255)
    years_of_experience: int | None = Field(default=None, ge=0, le=60)
    credential_urls: list[str] | None = None
    id_card_url: str | None = Field(default=None, max_length=512)
    tag_ids: list[int] | None = None


class TagOut(ApiModel):
    id: int
    name: str
    type: str


class ServiceOut(ApiModel):
    id: int
    name: str
    service_type: Literal["SINGLE", "PACKAGE"]
    duration_min: int
    price_in_cents: int
    description: str | None = None
    is_enabled: bool


class CoachProfileOut(ApiModel):
    id: int
    user_id: int
    real_name: str
    bio: str | None = None
    training_exp: str | None = None
    service_concept: str | None = None
    years_of_experience: int
    tags: list[TagOut] = []
    services: list[ServiceOut] = []
    audit_status: Literal["PENDING", "APPROVED", "REJECTED"]
    audit_remark: str | None = None
    rating: float = 0.0
    review_count: int = 0
    created_at: str


class AuditListOut(ApiModel):
    id: int
    coach_id: int
    coach_name: str
    submit_version: int
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    remark: str | None = None
    submitted_at: str
    reviewed_at: str | None = None


class AuditOut(ApiModel):
    id: int
    coach_id: int
    coach_name: str
    phone: str
    submit_version: int
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    remark: str | None = None
    snapshot: dict
    submitted_at: str
    reviewed_at: str | None = None


class AuditRejectIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
