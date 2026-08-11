"""平台公开信息（合规：心理援助热线 / 免责声明等）。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db
from app.api.response import ok
from app.schemas.system_config import PublicPlatformConfigOut
from app.services.system_config_service import get_public_config

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
