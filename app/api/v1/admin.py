from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.coach import AuditListOut, AuditOut, AuditRejectIn
from app.services.auth_service import mask_phone
from app.services.coach_service import (
    approve_audit,
    get_audit_or_404,
    list_audits,
    reject_audit,
)
from app.utils.time import to_iso

router = APIRouter(prefix="/admin/coach-audits", tags=["admin-coach-audits"])


def audit_list_to_out(audit, user) -> dict:
    return AuditListOut(
        id=audit.id,
        coach_id=audit.coach_id,
        coach_name=user.nickname,
        submit_version=audit.submit_version,
        status=audit.status,
        remark=audit.remark,
        submitted_at=to_iso(audit.submitted_at),
        reviewed_at=to_iso(audit.reviewed_at),
    ).model_dump(by_alias=True)


def audit_detail_to_out(audit, user) -> dict:
    return AuditOut(
        id=audit.id,
        coach_id=audit.coach_id,
        coach_name=user.nickname,
        phone=mask_phone(user.phone),
        submit_version=audit.submit_version,
        status=audit.status,
        remark=audit.remark,
        snapshot=audit.profile_snapshot,
        submitted_at=to_iso(audit.submitted_at),
        reviewed_at=to_iso(audit.reviewed_at),
    ).model_dump(by_alias=True)


@router.get("")
def list_audits_endpoint(
    request: Request,
    status: str | None = Query(default=None, pattern="^(PENDING|APPROVED|REJECTED)$"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    admin: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = list_audits(db, status, page, pageSize)
    items = [audit_list_to_out(audit, user) for audit, user in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/{audit_id}")
def audit_detail(
    audit_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> dict:
    audit, user = get_audit_or_404(db, audit_id)
    return ok(audit_detail_to_out(audit, user), trace_id=request.state.trace_id)


@router.post("/{audit_id}/approve")
def approve(
    audit_id: int,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> dict:
    audit = approve_audit(db, admin, audit_id)
    return ok({"id": audit.id, "status": audit.status}, trace_id=request.state.trace_id)


@router.post("/{audit_id}/reject")
def reject(
    audit_id: int,
    body: AuditRejectIn,
    request: Request,
    admin: User = Depends(require_role("ADMIN")),
    db: Session = Depends(get_db),
) -> dict:
    audit = reject_audit(db, admin, audit_id, body.reason)
    return ok({"id": audit.id, "status": audit.status, "remark": audit.remark}, trace_id=request.state.trace_id)
