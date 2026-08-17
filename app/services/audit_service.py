"""审计日志：敏感操作统一留痕 + 管理端查询。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    actor_user_id: int | None,
    actor_role: str,
    action: str,
    target_type: str,
    target_id: int | None = None,
    detail: dict | None = None,
    ip: str | None = None,
) -> None:
    """写入一条审计日志（由调用方随事务提交）。"""
    db.add(AuditLog(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip=ip,
    ))


async def list_audit_logs(
    db: AsyncSession,
    *,
    actor_user_id: int | None,
    action: str | None,
    target_type: str | None,
    page: int,
    page_size: int,
) -> tuple[list[AuditLog], int]:
    stmt = select(AuditLog)
    if actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total
