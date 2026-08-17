"""平台公开信息（合规：心理援助热线 / 免责声明等）。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.response import ok
from app.schemas.system_config import AgreementOut, PublicPlatformConfigOut
from app.services.system_config_service import get_config_value, get_public_config

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/config")
async def platform_config(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return ok(
        PublicPlatformConfigOut(**await get_public_config(db)).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )


@router.get("/agreement")
async def platform_agreement(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    version = await get_config_value(db, "agreement_version")
    content = await get_config_value(db, "agreement_content")
    return ok(
        AgreementOut(version=version, content=content).model_dump(by_alias=True),
        trace_id=request.state.trace_id,
    )
