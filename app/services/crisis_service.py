"""危机处理 SOP：关键词检测 → 建档 → 值班接管 → 跟进留痕 → 结案。"""

import os
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.crisis import CrisisFlag, CrisisFollowUp
from app.models.user import User
from app.services.notification_service import notify
from app.services.system_config_service import get_config_value
from app.utils.time import to_iso, utcnow_naive

CRISIS_KEYWORDS = [
    k.strip()
    for k in os.environ.get(
        "CRISIS_KEYWORDS",
        "自杀,想死,不想活,活不下去,结束生命,伤害自己,自残,不想存在,遗书,想离开这个世界,解脱",
    ).split(",")
    if k.strip()
]
DEDUP_WINDOW_MIN = 10


def detect_crisis(text: str | None) -> bool:
    if not text:
        return False
    return any(keyword in text for keyword in CRISIS_KEYWORDS)


async def maybe_flag_crisis(
    db: AsyncSession,
    user_id: int,
    source: str,
    text: str,
) -> CrisisFlag | None:
    """命中关键词则建档 + 通知管理员与用户；同来源 10 分钟内去重。"""
    if not detect_crisis(text):
        return None
    recent = (
        await db.scalar(
            select(func.count())
            .select_from(CrisisFlag)
            .where(
                CrisisFlag.user_id == user_id,
                CrisisFlag.source == source,
                CrisisFlag.created_at > utcnow_naive() - timedelta(minutes=DEDUP_WINDOW_MIN),
            )
        )
        or 0
    )
    if recent:
        return None

    flag = CrisisFlag(
        user_id=user_id,
        source=source,
        level="HIGH",
        content=text.strip()[:500],
        status="OPEN",
    )
    db.add(flag)
    await db.flush()
    db.add(CrisisFollowUp(
        crisis_id=flag.id,
        actor_id=None,
        actor_role="SYSTEM",
        action="DETECT",
        note="系统自动检测命中危机关键词",
    ))

    admins = list(
        await db.scalars(
            select(User).where(
                User.role == "ADMIN",
                User.status == "ENABLED",
                User.deleted_at.is_(None),
            )
        )
    )
    for admin in admins:
        await notify(
            db,
            admin.id,
            "CRISIS",
            "危机预警",
            f"用户 #{user_id} 在「{source}」出现危机表述，请尽快处理。",
        )
    hint = await get_config_value(db, "emergency_hint")
    await notify(
        db,
        user_id,
        "CRISIS",
        "紧急求助提示",
        hint or "如你正处于心理危机，请立即拨打心理援助热线 12356，或前往就近医疗机构。",
    )
    return flag


async def _get_flag_or_404(db: AsyncSession, crisis_id: int) -> CrisisFlag:
    flag = await db.get(CrisisFlag, crisis_id)
    if flag is None:
        raise AppError(404, "CRISIS_NOT_FOUND", "危机记录不存在")
    return flag


async def list_crisis_flags(
    db: AsyncSession,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[CrisisFlag], int]:
    stmt = select(CrisisFlag)
    if status:
        stmt = stmt.where(CrisisFlag.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(CrisisFlag.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def crisis_to_out(db: AsyncSession, flag: CrisisFlag, users: dict[int, User]) -> dict:
    user = users.get(flag.user_id)
    assignee = users.get(flag.assigned_admin_id) if flag.assigned_admin_id else None
    return {
        "id": flag.id,
        "user": {
            "id": flag.user_id,
            "nickname": user.nickname if user else "",
            "phone": user.phone if user else "",
        },
        "source": flag.source,
        "level": flag.level,
        "content": flag.content,
        "status": flag.status,
        "assignedAdminId": flag.assigned_admin_id,
        "assignedAdminName": assignee.nickname if assignee else "",
        "resolvedAt": to_iso(flag.resolved_at),
        "createdAt": to_iso(flag.created_at),
    }


async def assign_crisis(db: AsyncSession, admin: User, crisis_id: int) -> CrisisFlag:
    flag = await _get_flag_or_404(db, crisis_id)
    if flag.status == "RESOLVED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "已结案记录不可再指派")
    flag.status = "FOLLOWING"
    flag.assigned_admin_id = admin.id
    db.add(CrisisFollowUp(
        crisis_id=flag.id,
        actor_id=admin.id,
        actor_role="ADMIN",
        action="ASSIGN",
        note=f"{admin.nickname} 接管处理",
    ))
    await db.commit()
    await db.refresh(flag)
    return flag


async def add_crisis_follow_up(db: AsyncSession, admin: User, crisis_id: int, note: str) -> CrisisFollowUp:
    flag = await _get_flag_or_404(db, crisis_id)
    if flag.status == "RESOLVED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "已结案记录不可再跟进，请重新开启")
    if not note.strip():
        raise AppError(400, "VALIDATION_ERROR", "跟进内容不能为空")
    if flag.status == "OPEN":
        flag.status = "FOLLOWING"
        flag.assigned_admin_id = admin.id
    follow_up = CrisisFollowUp(
        crisis_id=flag.id,
        actor_id=admin.id,
        actor_role="ADMIN",
        action="FOLLOW_UP",
        note=note.strip()[:500],
    )
    db.add(follow_up)
    await db.commit()
    await db.refresh(follow_up)
    return follow_up


async def resolve_crisis(db: AsyncSession, admin: User, crisis_id: int, note: str) -> CrisisFlag:
    flag = await _get_flag_or_404(db, crisis_id)
    if flag.status == "RESOLVED":
        raise AppError(409, "INVALID_STATE_TRANSITION", "该记录已结案")
    flag.status = "RESOLVED"
    flag.resolved_at = utcnow_naive()
    db.add(CrisisFollowUp(
        crisis_id=flag.id,
        actor_id=admin.id,
        actor_role="ADMIN",
        action="RESOLVE",
        note=note.strip()[:500] or "结案",
    ))
    await db.commit()
    await db.refresh(flag)
    return flag


async def list_crisis_follow_ups(db: AsyncSession, crisis_id: int, users: dict[int, User]) -> list[dict]:
    await _get_flag_or_404(db, crisis_id)
    rows = list(
        await db.scalars(
            select(CrisisFollowUp)
            .where(CrisisFollowUp.crisis_id == crisis_id)
            .order_by(CrisisFollowUp.created_at.asc())
        )
    )
    return [
        {
            "id": r.id,
            "actorId": r.actor_id,
            "actorRole": r.actor_role,
            "actorName": users[r.actor_id].nickname if r.actor_id in users else "系统",
            "action": r.action,
            "note": r.note,
            "createdAt": to_iso(r.created_at),
        }
        for r in rows
    ]
