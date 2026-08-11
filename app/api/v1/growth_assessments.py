"""成长测评：量表、提交、结果与历史。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok, paginated
from app.models.user import User
from app.schemas.growth_assessment import (
    AssessmentHistoryItemOut,
    AssessmentResultOut,
    AssessmentSubmitIn,
    GrowthTemplateOut,
)
from app.services.growth_assessment_service import (
    get_result_or_404,
    get_template_payload,
    list_results,
    submit_assessment,
)

router = APIRouter(prefix="/growth-assessments", tags=["growth-assessments"])


@router.get("/template")
async def assessment_template(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        GrowthTemplateOut(**await get_template_payload(db)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.post("", status_code=201)
async def submit(
    body: AssessmentSubmitIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        AssessmentResultOut(**await submit_assessment(db, user.id, body.answers)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("")
async def history(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=50, alias="pageSize"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    rows, total = await list_results(db, user.id, page, pageSize)
    items = [AssessmentHistoryItemOut(**item).model_dump(by_alias=True) for item in rows]
    return ok(paginated(items, total, page, pageSize), trace_id=request.state.trace_id)


@router.get("/{result_id}")
async def detail(
    result_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        AssessmentResultOut(**await get_result_or_404(db, user.id, result_id)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )
