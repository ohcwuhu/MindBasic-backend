from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_coach, get_current_user, get_db
from app.api.response import ok, paginated
from app.models.coach import CoachProfile
from app.models.user import User
from app.schemas.coach import CoachProfileIn, CoachProfileOut, CoachProfilePatchIn
from app.schemas.appointment import (
    AppointmentStatusOut,
    CancelAppointmentIn,
    CoachAppointmentOut,
)
from app.schemas.case import CaseRecordIn, CaseRecordOut, CaseRecordPatchIn, CaseStatsOut
from app.services.coach_service import (
    create_profile,
    get_profile_or_404,
    profile_to_out,
    submit_audit,
    update_profile,
)
from app.services.appointment_service import (
    cancel_coach_appointment,
    coach_appointment_to_out,
    complete_appointment,
    confirm_appointment,
    list_coach_appointments,
)
from app.services.case_record_service import (
    case_stats,
    case_to_out,
    create_case,
    delete_case,
    get_own_case_or_404,
    list_cases,
    update_case,
)

router = APIRouter(prefix="/coach", tags=["coach"])


@router.post("/profile", status_code=201)
def submit_profile(
    body: CoachProfileIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = create_profile(db, user, body)
    return ok(
        CoachProfileOut(**profile_to_out(db, profile)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/profile")
def get_profile(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = get_profile_or_404(db, user.id)
    return ok(
        CoachProfileOut(**profile_to_out(db, profile)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.patch("/profile")
def patch_profile(
    body: CoachProfilePatchIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = update_profile(db, user, body)
    return ok(
        CoachProfileOut(**profile_to_out(db, profile)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("/profile/submit-audit")
def resubmit_audit(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    profile = submit_audit(db, user)
    return ok(
        CoachProfileOut(**profile_to_out(db, profile)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


# ---------- 预约管理 ----------


@router.get("/appointments")
def coach_appointments(
    request: Request,
    status: str | None = Query(default=None, pattern="^(PENDING|CONFIRMED|COMPLETED|CANCELLED)$"),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_coach_appointments(db, coach.id, status, date, page, pageSize)
    items = [CoachAppointmentOut(**coach_appointment_to_out(db, a)).model_dump(by_alias=True) for a in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/appointments/{appointment_id}/confirm")
def coach_confirm(
    appointment_id: int,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    appointment = confirm_appointment(db, coach.id, appointment_id)
    return ok(
        AppointmentStatusOut(id=appointment.id, status=appointment.status).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("/appointments/{appointment_id}/cancel")
def coach_cancel(
    appointment_id: int,
    body: CancelAppointmentIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    appointment = cancel_coach_appointment(db, coach.id, appointment_id, body.cancel_reason, coach.user_id)
    return ok(
        AppointmentStatusOut(
            id=appointment.id,
            status=appointment.status,
            cancel_reason=appointment.cancel_reason,
        ).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("/appointments/{appointment_id}/complete")
def coach_complete(
    appointment_id: int,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    appointment = complete_appointment(db, coach.id, appointment_id)
    return ok(
        AppointmentStatusOut(
            id=appointment.id,
            status=appointment.status,
            completed_at=appointment.completed_at.isoformat() if appointment.completed_at else None,
        ).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


# ---------- 个案记录 ----------


@router.get("/cases/stats")
def coach_case_stats(
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    return ok(
        CaseStatsOut(**case_stats(db, coach.id)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/cases")
def coach_cases(
    request: Request,
    keyword: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_cases(db, coach.id, keyword, page, pageSize)
    items = [CaseRecordOut(**case_to_out(r)).model_dump(by_alias=True) for r in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.post("/cases", status_code=201)
def coach_create_case(
    body: CaseRecordIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    record = create_case(db, coach.id, body)
    return ok(CaseRecordOut(**case_to_out(record)).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.get("/cases/{case_id}")
def coach_case_detail(
    case_id: int,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    record = get_own_case_or_404(db, coach.id, case_id)
    return ok(CaseRecordOut(**case_to_out(record)).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.patch("/cases/{case_id}")
def coach_update_case(
    case_id: int,
    body: CaseRecordPatchIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    record = update_case(db, coach.id, case_id, body)
    return ok(CaseRecordOut(**case_to_out(record)).model_dump(by_alias=True), trace_id=request.state.trace_id)


@router.delete("/cases/{case_id}", status_code=204)
def coach_delete_case(
    case_id: int,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> None:
    delete_case(db, coach.id, case_id)
