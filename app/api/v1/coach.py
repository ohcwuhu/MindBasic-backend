from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_coach, get_current_user, get_db
from app.api.response import ok, paginated
from app.models.coach import CoachProfile
from app.models.user import User
from app.schemas.coach import CoachProfileIn, CoachProfileOut, CoachProfilePatchIn
from app.schemas.coach import (
    ClientOut,
    ClientPatchIn,
    PhraseIn,
    PhraseOut,
    PhrasePatchIn,
    SavePhraseIn,
    ServiceIn,
    ServiceOut,
    ServicePatchIn,
    SlotBatchIn,
    SlotOut,
)
from app.schemas.appointment import (
    AppointmentStatusOut,
    CancelAppointmentIn,
    CoachAppointmentOut,
)
from app.schemas.case import CaseRecordIn, CaseRecordOut, CaseRecordPatchIn, CaseStatsOut
from app.services.coach_service import (
    create_service,
    create_profile,
    create_phrase,
    delete_phrase,
    get_own_service_or_404,
    get_own_phrase_or_404,
    get_profile_or_404,
    list_clients,
    list_coach_slots,
    list_platform_phrases,
    my_phrases,
    profile_to_out,
    replace_coach_slots,
    save_platform_phrase,
    submit_audit,
    update_client_remark,
    update_phrase,
    update_service,
    update_profile,
)
from app.utils.time import to_iso
from app.services.appointment_service import (
    cancel_coach_appointment,
    coach_appointment_to_out,
    coach_appointments_to_out,
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


# ---------- 服务项目 ----------


@router.get("/services")
def coach_services(
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.coach_service import get_coach_services

    items = [
        ServiceOut(**{
            "id": s.id,
            "name": s.name,
            "service_type": s.service_type,
            "duration_min": s.duration_min,
            "price_in_cents": s.price_in_cents,
            "description": s.description,
            "is_enabled": bool(s.is_enabled),
        }).model_dump(by_alias=True)
        for s in get_coach_services(db, coach.id)
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.post("/services", status_code=201)
def coach_create_service(
    body: ServiceIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    service = create_service(db, coach.id, body)
    return ok(
        ServiceOut(
            id=service.id,
            name=service.name,
            service_type=service.service_type,
            duration_min=service.duration_min,
            price_in_cents=service.price_in_cents,
            description=service.description,
            is_enabled=True,
        ).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.patch("/services/{service_id}")
def coach_update_service(
    service_id: int,
    body: ServicePatchIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    service = update_service(db, coach.id, service_id, body)
    return ok(
        ServiceOut(
            id=service.id,
            name=service.name,
            service_type=service.service_type,
            duration_min=service.duration_min,
            price_in_cents=service.price_in_cents,
            description=service.description,
            is_enabled=bool(service.is_enabled),
        ).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


# ---------- 时段设置 ----------


@router.get("/slots")
def coach_slots(
    request: Request,
    startDate: str = Query(default="", alias="startDate"),
    endDate: str = Query(default="", alias="endDate"),
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    from datetime import date, timedelta

    start = startDate or date.today().isoformat()
    end = endDate or (date.today() + timedelta(days=14)).isoformat()
    slots = list_coach_slots(db, coach.id, start, end)
    items = [
        SlotOut(
            id=s.id,
            coach_id=s.coach_id,
            date=s.date.isoformat(),
            start_time=s.start_time.strftime("%H:%M"),
            end_time=s.end_time.strftime("%H:%M"),
            status=s.status,
        ).model_dump(by_alias=True)
        for s in slots
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.put("/slots")
def coach_replace_slots(
    body: SlotBatchIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    slots = replace_coach_slots(db, coach.id, body)
    items = [
        SlotOut(
            id=s.id,
            coach_id=s.coach_id,
            date=s.date.isoformat(),
            start_time=s.start_time.strftime("%H:%M"),
            end_time=s.end_time.strftime("%H:%M"),
            status=s.status,
        ).model_dump(by_alias=True)
        for s in slots
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)


# ---------- 客户管理 ----------


@router.get("/clients")
def coach_clients(
    request: Request,
    keyword: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_clients(db, coach.id, keyword, page, pageSize)
    from app.services.auth_service import mask_phone

    items = [
        ClientOut(
            id=relation.id,
            user_id=user.id,
            nickname=user.nickname,
            phone=mask_phone(user.phone),
            last_appointment_at=to_iso(relation.last_appointment_at),
            remark=relation.remark,
        ).model_dump(by_alias=True)
        for relation, user in rows
    ]
    return ok({"items": items, "pagination": {"page": page, "pageSize": pageSize, "totalItems": total, "totalPages": max(1, (total + pageSize - 1) // pageSize), "hasMore": page * pageSize < total}}, trace_id=request.state.trace_id)


@router.patch("/clients/{relation_id}")
def coach_update_client(
    relation_id: int,
    body: ClientPatchIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    relation = update_client_remark(db, coach.id, relation_id, body.remark)
    return ok({"id": relation.id, "remark": relation.remark}, trace_id=request.state.trace_id)


# ---------- 话术库 ----------


@router.get("/phrases")
def coach_phrases_endpoint(
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    items = [
        PhraseOut(
            id=p.id,
            category=p.category,
            content=p.content,
            source=p.source,
            created_at=to_iso(p.created_at),
        ).model_dump(by_alias=True)
        for p in my_phrases(db, coach.id)
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)


@router.post("/phrases", status_code=201)
def coach_create_phrase_endpoint(
    body: PhraseIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    phrase = create_phrase(db, coach.id, body.category, body.content)
    return ok(
        PhraseOut(
            id=phrase.id,
            category=phrase.category,
            content=phrase.content,
            source=phrase.source,
            created_at=to_iso(phrase.created_at),
        ).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("/phrases/save", status_code=201)
def coach_save_phrase(
    body: SavePhraseIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    phrase = save_platform_phrase(db, coach.id, body.phrase_id)
    return ok(
        PhraseOut(
            id=phrase.id,
            category=phrase.category,
            content=phrase.content,
            source=phrase.source,
            created_at=to_iso(phrase.created_at),
        ).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.patch("/phrases/{phrase_id}")
def coach_update_phrase_endpoint(
    phrase_id: int,
    body: PhrasePatchIn,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    phrase = update_phrase(db, coach.id, phrase_id, body.category, body.content)
    return ok(
        PhraseOut(
            id=phrase.id,
            category=phrase.category,
            content=phrase.content,
            source=phrase.source,
            created_at=to_iso(phrase.created_at),
        ).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.delete("/phrases/{phrase_id}", status_code=204)
def coach_delete_phrase_endpoint(
    phrase_id: int,
    request: Request,
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> None:
    delete_phrase(db, coach.id, phrase_id)


phrase_library_router = APIRouter(prefix="/phrase-library", tags=["phrase-library"])


@phrase_library_router.get("")
def platform_phrases_endpoint(
    request: Request,
    category: str | None = Query(default=None, pattern="^(OPENING|RESOURCE|FUTURE|ACTION|OTHER)$"),
    coach: CoachProfile = Depends(get_current_coach),
    db: Session = Depends(get_db),
) -> dict:
    items = [
        {"id": p.id, "category": p.category, "content": p.content}
        for p in list_platform_phrases(db, category)
    ]
    return ok({"items": items}, trace_id=request.state.trace_id)


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
    items = [CoachAppointmentOut(**item).model_dump(by_alias=True) for item in coach_appointments_to_out(db, rows)]
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
