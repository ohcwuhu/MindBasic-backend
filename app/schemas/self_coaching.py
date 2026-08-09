from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel


class TemplateStepOut(ApiModel):
    step_key: Literal["STATUS", "IDEAL", "RESOURCES", "ACTION"]
    step_name: str
    question: str
    placeholder: str | None = None


class TemplateOut(ApiModel):
    id: int
    name: str
    scene: str
    description: str | None = None
    steps: list[TemplateStepOut] = []


class SelfCoachingRecordIn(ApiModel):
    template_id: int
    answers: dict[str, str] = Field(default_factory=dict)
    status: Literal["DRAFT", "COMPLETED"] = "DRAFT"


class SelfCoachingRecordPatchIn(ApiModel):
    answers: dict[str, str] | None = None
    status: Literal["DRAFT", "COMPLETED"] | None = None


class ActionCardOut(ApiModel):
    title: str
    content: str
    shareImageUrl: str | None = None


class SelfCoachingRecordOut(ApiModel):
    id: int
    template_id: int
    answers: dict[str, str]
    action_card: ActionCardOut | None = None
    status: Literal["DRAFT", "COMPLETED"]
    created_at: str
    updated_at: str


class SelfCoachingRecordListOut(ApiModel):
    id: int
    template_id: int
    template_name: str
    status: Literal["DRAFT", "COMPLETED"]
    created_at: str
    updated_at: str
