from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok, paginated
from app.core.exceptions import AppError
from app.models.coach import CoachProfile
from app.models.user import User
from app.schemas.appointment import (
    AppointmentCreateIn,
    AppointmentOut,
    CancelAppointmentIn,
    RescheduleAppointmentIn,
)
from app.schemas.review import ReviewIn, ReviewOut
from app.services.appointment_service import (
    cancel_my_appointment,
    create_appointment,
    get_appointment_ctx,
    list_my_appointments,
    mark_no_show_appointment,
    my_appointments_to_out,
    reschedule_appointment,
)
from app.services.review_service import create_review, get_my_review, review_to_out

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("", status_code=201)
async def book_appointment(
    body: AppointmentCreateIn,
    request: Request,
    idempotencyKey: str | None = Header(default=None, max_length=36, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    appointment, created = await create_appointment(db, user, body, idempotencyKey)
    payload = AppointmentOut(**await get_appointment_ctx(db, appointment)).model_dump(by_alias=True)
    if not created:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=200,
            content={"code": "OK", "message": "success", "data": payload, "traceId": request.state.trace_id},
        )
    return ok(payload, trace_id=request.state.trace_id)


@router.get("/mine")
async def my_appointments(
    request: Request,
    status: str | None = Query(default=None, pattern="^(PENDING|CONFIRMED|COMPLETED|CANCELLED|NO_SHOW|RESCHEDULED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_my_appointments(db, user.id, status, page, pageSize)
    items = [AppointmentOut(**item).model_dump(by_alias=True) for item in await my_appointments_to_out(db, rows)]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/{appointment_id}/cancel")
async def cancel_appointment(
    appointment_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    body: CancelAppointmentIn | None = None,
) -> dict:
    appointment = await cancel_my_appointment(
        db, user, appointment_id, reason=body.cancel_reason if body else None
    )
    return ok(
        AppointmentOut(**await get_appointment_ctx(db, appointment)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("/{appointment_id}/reschedule")
async def reschedule_appointment_route(
    appointment_id: int,
    body: RescheduleAppointmentIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    old_appointment, new_appointment = await reschedule_appointment(
        db, user, appointment_id, body.slot_id, body.service_id
    )
    return ok({
        "old": AppointmentOut(**await get_appointment_ctx(db, old_appointment)).model_dump(by_alias=True),
        "new": AppointmentOut(**await get_appointment_ctx(db, new_appointment)).model_dump(by_alias=True),
    }, trace_id=request.state.trace_id)


@router.post("/{appointment_id}/no-show")
async def mark_no_show_route(
    appointment_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    if user.role == "ADMIN":
        appointment = await mark_no_show_appointment(db, user, appointment_id)
    else:
        profile = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user.id))
        if profile is None or profile.audit_status != "APPROVED":
            raise AppError(403, "COACH_NOT_APPROVED", "仅教练或管理员可标记未赴约")
        appointment = await mark_no_show_appointment(db, user, appointment_id, profile.id)
    return ok(
        AppointmentOut(**await get_appointment_ctx(db, appointment)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("/{appointment_id}/review", status_code=201)
async def review_appointment(
    appointment_id: int,
    body: ReviewIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    review = await create_review(db, user, appointment_id, body.rating, body.content)
    return ok(
        ReviewOut(**review_to_out(review, user.nickname)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/{appointment_id}/review")
async def my_appointment_review(
    appointment_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    review = await get_my_review(db, user.id, appointment_id)
    if review is None:
        from app.core.exceptions import AppError

        raise AppError(404, "NOT_FOUND", "还没有评价")
    return ok(
        ReviewOut(**review_to_out(review, user.nickname)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )
