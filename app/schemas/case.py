from pydantic import Field

from app.schemas.base import ApiModel


class CaseRecordIn(ApiModel):
    appointment_id: int | None = None
    client_nickname: str | None = Field(default=None, max_length=32)
    key_points: str | None = Field(default=None, max_length=4000)
    user_gains: str | None = Field(default=None, max_length=4000)
    followup_advice: str | None = Field(default=None, max_length=4000)
    duration_min: int = Field(default=0, ge=0, le=24 * 60)


class CaseRecordPatchIn(ApiModel):
    client_nickname: str | None = Field(default=None, max_length=32)
    key_points: str | None = Field(default=None, max_length=4000)
    user_gains: str | None = Field(default=None, max_length=4000)
    followup_advice: str | None = Field(default=None, max_length=4000)
    duration_min: int | None = Field(default=None, ge=0, le=24 * 60)


class CaseRecordOut(ApiModel):
    id: int
    appointment_id: int | None = None
    client_nickname: str | None = None
    key_points: str | None = None
    user_gains: str | None = None
    followup_advice: str | None = None
    duration_min: int
    created_at: str
    updated_at: str


class CaseStatsOut(ApiModel):
    total_cases: int
    service_minutes: int
    client_count: int
