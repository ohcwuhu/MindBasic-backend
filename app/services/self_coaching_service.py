"""自我教练业务逻辑。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError
from app.models.growth import CoachingTemplate, SelfCoachingRecord, TemplateStep
from app.utils.time import to_iso


def get_template_or_404(db: Session, template_id: int) -> CoachingTemplate:
    template = db.get(CoachingTemplate, template_id)
    if template is None or not template.is_enabled:
        raise AppError(404, "TEMPLATE_NOT_FOUND", "自我教练模板不存在")
    return template


def list_templates(db: Session) -> list[CoachingTemplate]:
    stmt = (
        select(CoachingTemplate)
        .where(CoachingTemplate.is_enabled.is_(True))
        .order_by(CoachingTemplate.sort_order)
    )
    return list(db.scalars(stmt))


def list_steps(db: Session, template_id: int) -> list[TemplateStep]:
    stmt = (
        select(TemplateStep)
        .where(TemplateStep.template_id == template_id)
        .order_by(TemplateStep.sort_order)
    )
    return list(db.scalars(stmt))


def validate_answers(template: CoachingTemplate, steps: list[TemplateStep], answers: dict[str, str]) -> dict[str, str]:
    valid_keys = {step.step_key for step in steps}
    cleaned: dict[str, str] = {}
    for key, value in answers.items():
        if key not in valid_keys:
            raise AppError(400, "VALIDATION_ERROR", f"无效的步骤标识：{key}")
        value = value.strip()
        if not value:
            raise AppError(400, "VALIDATION_ERROR", f"步骤 {key} 的回答不能为空")
        if len(value) > 2000:
            raise AppError(400, "VALIDATION_ERROR", f"步骤 {key} 的回答过长")
        cleaned[key] = value
    return cleaned


def require_all_steps(steps: list[TemplateStep], answers: dict[str, str]) -> None:
    missing = [step.step_name for step in steps if not answers.get(step.step_key)]
    if missing:
        raise AppError(400, "VALIDATION_ERROR", f"请完成以下步骤后再生成行动卡：{'、'.join(missing)}")


def build_action_card(template: CoachingTemplate, steps: list[TemplateStep], answers: dict[str, str]) -> dict:
    labels = {step.step_key: step.step_name for step in steps}
    lines = [f"{labels.get(key, key)}：{value}" for key, value in answers.items() if value]
    return {
        "title": f"{template.name} · 成长行动卡",
        "content": "\n".join(lines),
        "shareImageUrl": None,
    }


def get_own_record_or_404(db: Session, user_id: int, record_id: int) -> SelfCoachingRecord:
    record = db.scalar(
        select(SelfCoachingRecord).where(
            SelfCoachingRecord.id == record_id,
            SelfCoachingRecord.user_id == user_id,
        )
    )
    if record is None:
        raise AppError(404, "NOT_FOUND", "记录不存在")
    return record


def record_to_out(record: SelfCoachingRecord) -> dict:
    return {
        "id": record.id,
        "template_id": record.template_id,
        "answers": record.answers,
        "action_card": record.action_card,
        "status": record.status,
        "created_at": to_iso(record.created_at),
        "updated_at": to_iso(record.updated_at),
    }


def count_records(db: Session, user_id: int, status: str | None) -> int:
    stmt = select(func.count()).select_from(SelfCoachingRecord).where(SelfCoachingRecord.user_id == user_id)
    if status:
        stmt = stmt.where(SelfCoachingRecord.status == status)
    return db.scalar(stmt) or 0
