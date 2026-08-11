from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.models.growth import CoachingTemplate, SelfCoachingRecord
from app.models.user import User
from app.schemas.self_coaching import (
    SelfCoachingRecordIn,
    SelfCoachingRecordOut,
    SelfCoachingRecordPatchIn,
    SelfCoachingRecordListOut,
    TemplateOut,
    TemplateStepOut,
)
from app.services.self_coaching_service import (
    build_action_card,
    count_records,
    get_own_record_or_404,
    get_template_or_404,
    list_steps,
    list_templates,
    record_to_out,
    require_all_steps,
    validate_answers,
)
from app.utils.time import to_iso

router = APIRouter(prefix="/self-coaching", tags=["self-coaching"])


def template_to_out(template: CoachingTemplate, steps) -> dict:
    return TemplateOut(
        id=template.id,
        name=template.name,
        scene=template.scene,
        description=template.description,
        steps=[TemplateStepOut(**{
            "step_key": step.step_key,
            "step_name": step.step_name,
            "question": step.question,
            "placeholder": step.placeholder,
        }) for step in steps],
    ).model_dump(by_alias=True)


@router.get("/templates")
async def get_templates(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    templates = await list_templates(db)
    items = [template_to_out(t, await list_steps(db, t.id)) for t in templates]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.get("/templates/{template_id}")
async def get_template(
    template_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    template = await get_template_or_404(db, template_id)
    return ok(template_to_out(template, await list_steps(db, template.id)), trace_id=request.state.trace_id)


@router.post("/records", status_code=201)
async def create_record(
    body: SelfCoachingRecordIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    template = await get_template_or_404(db, body.template_id)
    steps = await list_steps(db, template.id)
    answers = validate_answers(template, steps, body.answers)
    action_card = None
    if body.status == "COMPLETED":
        require_all_steps(steps, answers)
        action_card = build_action_card(template, steps, answers)
    record = SelfCoachingRecord(
        user_id=user.id,
        template_id=template.id,
        answers=answers,
        action_card=action_card,
        status=body.status,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return ok(
        SelfCoachingRecordOut(**record_to_out(record)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.patch("/records/{record_id}")
async def patch_record(
    record_id: int,
    body: SelfCoachingRecordPatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    record = await get_own_record_or_404(db, user.id, record_id)
    template = await get_template_or_404(db, record.template_id)
    steps = await list_steps(db, template.id)

    answers = dict(record.answers)
    if body.answers is not None:
        answers.update(validate_answers(template, steps, body.answers))

    new_status = body.status or record.status
    if record.status == "COMPLETED" and new_status == "DRAFT":
        raise AppError(409, "INVALID_STATE_TRANSITION", "已完成记录不可退回草稿")

    if new_status == "COMPLETED":
        require_all_steps(steps, answers)
        record.action_card = build_action_card(template, steps, answers)
    else:
        record.action_card = None

    record.answers = answers
    record.status = new_status
    await db.commit()
    await db.refresh(record)
    return ok(
        SelfCoachingRecordOut(**record_to_out(record)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/records")
async def list_records(
    request: Request,
    status: str | None = Query(default=None, pattern="^(DRAFT|COMPLETED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    stmt = (
        select(SelfCoachingRecord, CoachingTemplate.name)
        .join(CoachingTemplate, CoachingTemplate.id == SelfCoachingRecord.template_id)
        .where(SelfCoachingRecord.user_id == user.id)
    )
    if status:
        stmt = stmt.where(SelfCoachingRecord.status == status)
    stmt = (
        stmt.order_by(SelfCoachingRecord.created_at.desc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
    )
    rows = (await db.execute(stmt)).all()
    items = [
        SelfCoachingRecordListOut(
            id=record.id,
            template_id=record.template_id,
            template_name=template_name,
            status=record.status,
            created_at=to_iso(record.created_at),
            updated_at=to_iso(record.updated_at),
        ).model_dump(by_alias=True)
        for record, template_name in rows
    ]
    total = await count_records(db, user.id, status)
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/records/{record_id}")
async def get_record(
    record_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    record = await get_own_record_or_404(db, user.id, record_id)
    return ok(
        SelfCoachingRecordOut(**record_to_out(record)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )
