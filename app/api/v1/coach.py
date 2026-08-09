from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok
from app.models.user import User
from app.schemas.coach import CoachProfileIn, CoachProfileOut, CoachProfilePatchIn
from app.services.coach_service import (
    create_profile,
    get_profile_or_404,
    profile_to_out,
    submit_audit,
    update_profile,
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
