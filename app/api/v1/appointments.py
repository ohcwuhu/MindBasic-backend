from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.appointment import AppointmentCreateIn, AppointmentOut
from app.schemas.review import ReviewIn, ReviewOut
from app.services.appointment_service import (
    cancel_my_appointment,
    create_appointment,
    get_appointment_ctx,
    list_my_appointments,
    my_appointments_to_out,
)
from app.services.review_service import create_review, get_my_review, review_to_out

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("", status_code=201)
def book_appointment(
    body: AppointmentCreateIn,
    request: Request,
    idempotencyKey: str | None = Header(default=None, max_length=36, alias="Idempotency-Key"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    appointment, created = create_appointment(db, user, body, idempotencyKey)
    payload = AppointmentOut(**get_appointment_ctx(db, appointment)).model_dump(by_alias=True)
    if not created:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=200,
            content={"code": "OK", "message": "success", "data": payload, "traceId": request.state.trace_id},
        )
    return ok(payload, trace_id=request.state.trace_id)


@router.get("/mine")
def my_appointments(
    request: Request,
    status: str | None = Query(default=None, pattern="^(PENDING|CONFIRMED|COMPLETED|CANCELLED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_my_appointments(db, user.id, status, page, pageSize)
    items = [AppointmentOut(**item).model_dump(by_alias=True) for item in my_appointments_to_out(db, rows)]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/{appointment_id}/cancel")
def cancel_appointment(
    appointment_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    appointment = cancel_my_appointment(db, user, appointment_id)
    return ok(
        AppointmentOut(**get_appointment_ctx(db, appointment)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("/{appointment_id}/review", status_code=201)
def review_appointment(
    appointment_id: int,
    body: ReviewIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    review = create_review(db, user, appointment_id, body.rating, body.content)
    return ok(
        ReviewOut(**review_to_out(review, user.nickname)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/{appointment_id}/review")
def my_appointment_review(
    appointment_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    review = get_my_review(db, user.id, appointment_id)
    if review is None:
        from app.core.exceptions import AppError

        raise AppError(404, "NOT_FOUND", "还没有评价")
    return ok(
        ReviewOut(**review_to_out(review, user.nickname)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )
