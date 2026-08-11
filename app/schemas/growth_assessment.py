"""成长测评：量表、提交、结果与历史。"""

from pydantic import Field

from app.schemas.base import ApiModel


class QuestionOptionOut(ApiModel):
    value: int
    label: str


class GrowthQuestionOut(ApiModel):
    id: int
    dimension_key: str
    dimension_name: str
    question: str
    options: list[QuestionOptionOut]
    sort_order: int


class GrowthTemplateOut(ApiModel):
    id: int
    name: str
    description: str | None = None
    version: int
    questions: list[GrowthQuestionOut]


class AssessmentSubmitIn(ApiModel):
    answers: dict[str, int] = Field(min_length=1)


class DimensionScoreOut(ApiModel):
    dimension_key: str
    dimension_name: str
    score: float
    level: str
    level_label: str


class AssessmentResultOut(ApiModel):
    id: int
    template_id: int
    template_name: str
    scores: list[DimensionScoreOut]
    report: dict
    created_at: str


class AssessmentHistoryItemOut(ApiModel):
    id: int
    template_name: str
    created_at: str
