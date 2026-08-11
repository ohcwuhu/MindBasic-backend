from pydantic import Field

from app.schemas.base import ApiModel


class CaseRecordIn(ApiModel):
    appointment_id: int | None = None
    client_nickname: str | None = Field(default=None, max_length=32)
    content: str = Field(min_length=1, max_length=20000)
    duration_min: int = Field(default=0, ge=0, le=24 * 60)


class CaseRecordPatchIn(ApiModel):
    client_nickname: str | None = Field(default=None, max_length=32)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    duration_min: int | None = Field(default=None, ge=0, le=24 * 60)


class CaseRecordOut(ApiModel):
    id: int
    appointment_id: int | None = None
    client_nickname: str | None = None
    content: str | None = None
    duration_min: int
    created_at: str
    updated_at: str


class CaseStatsOut(ApiModel):
    total_cases: int
    service_minutes: int
    client_count: int
