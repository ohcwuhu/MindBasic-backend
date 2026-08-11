"""个案记录业务逻辑。"""

import csv
import io

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.models.coach import Appointment, CaseRecord
from app.schemas.case import CaseRecordIn, CaseRecordPatchIn
from app.utils.time import to_iso


async def get_own_case_or_404(db: AsyncSession, coach_profile_id: int, case_id: int) -> CaseRecord:
    record = await db.scalar(
        select(CaseRecord).where(
            CaseRecord.id == case_id,
            CaseRecord.coach_id == coach_profile_id,
        )
    )
    if record is None:
        raise AppError(404, "CASE_NOT_FOUND", "个案记录不存在")
    return record


async def create_case(db: AsyncSession, coach_profile_id: int, data: CaseRecordIn) -> CaseRecord:
    if data.appointment_id is not None:
        appointment = await db.scalar(
            select(Appointment).where(
                Appointment.id == data.appointment_id,
                Appointment.coach_id == coach_profile_id,
            )
        )
        if appointment is None:
            raise AppError(404, "APPOINTMENT_NOT_FOUND", "预约记录不存在")
        if appointment.status != "COMPLETED":
            raise AppError(400, "INVALID_STATE_TRANSITION", "仅可为已完成预约创建个案记录")
        duplicate = await db.scalar(
            select(CaseRecord.id).where(CaseRecord.appointment_id == data.appointment_id)
        )
        if duplicate is not None:
            raise AppError(409, "CONFLICT", "该预约已有个案记录")

    record = CaseRecord(
        coach_id=coach_profile_id,
        appointment_id=data.appointment_id,
        client_nickname=data.client_nickname,
        key_points=data.key_points,
        user_gains=data.user_gains,
        followup_advice=data.followup_advice,
        duration_min=data.duration_min,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_cases(
    db: AsyncSession, coach_profile_id: int, keyword: str | None, page: int, page_size: int
) -> tuple[list[CaseRecord], int]:
    stmt = select(CaseRecord).where(CaseRecord.coach_id == coach_profile_id)
    if keyword:
        stmt = stmt.where(CaseRecord.client_nickname.like(f"%{keyword}%"))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(CaseRecord.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return rows, total


async def list_all_cases(
    db: AsyncSession, coach_profile_id: int, limit: int = 10000
) -> list[CaseRecord]:
    """导出用：一次取该教练全部个案（按时间倒序，上限防内存失控）。"""
    return list(
        await db.scalars(
            select(CaseRecord)
            .where(CaseRecord.coach_id == coach_profile_id)
            .order_by(CaseRecord.created_at.desc())
            .limit(limit)
        )
    )


def cases_to_csv(records: list[CaseRecord]) -> str:
    """构建 UTF-8 BOM CSV（Excel 兼容）。"""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["个案编号", "客户称呼", "记录时间", "对话要点", "用户收获", "后续建议", "服务时长(分钟)", "关联预约ID"])
    for record in records:
        writer.writerow([
            record.id,
            record.client_nickname or "",
            to_iso(record.created_at),
            record.key_points or "",
            record.user_gains or "",
            record.followup_advice or "",
            record.duration_min,
            record.appointment_id or "",
        ])
    return "\ufeff" + buf.getvalue()


async def update_case(
    db: AsyncSession, coach_profile_id: int, case_id: int, data: CaseRecordPatchIn
) -> CaseRecord:
    record = await get_own_case_or_404(db, coach_profile_id, case_id)
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    for field in (
        "client_nickname",
        "key_points",
        "user_gains",
        "followup_advice",
        "duration_min",
    ):
        if field in changes:
            setattr(record, field, changes[field])
    await db.commit()
    await db.refresh(record)
    return record


async def delete_case(db: AsyncSession, coach_profile_id: int, case_id: int) -> None:
    record = await get_own_case_or_404(db, coach_profile_id, case_id)
    await db.delete(record)
    await db.commit()


async def case_stats(db: AsyncSession, coach_profile_id: int) -> dict:
    total_cases = (
        await db.scalar(
            select(func.count()).select_from(CaseRecord).where(CaseRecord.coach_id == coach_profile_id)
        )
        or 0
    )
    service_minutes = (
        await db.scalar(
            select(func.coalesce(func.sum(CaseRecord.duration_min), 0)).where(
                CaseRecord.coach_id == coach_profile_id
            )
        )
        or 0
    )
    client_count = (
        await db.scalar(
            select(func.count(func.distinct(CaseRecord.client_nickname))).where(
                CaseRecord.coach_id == coach_profile_id,
                CaseRecord.client_nickname.is_not(None),
                CaseRecord.client_nickname != "",
            )
        )
        or 0
    )
    return {
        "total_cases": total_cases,
        "service_minutes": service_minutes,
        "client_count": client_count,
    }


def case_to_out(record: CaseRecord) -> dict:
    return {
        "id": record.id,
        "appointment_id": record.appointment_id,
        "client_nickname": record.client_nickname,
        "key_points": record.key_points,
        "user_gains": record.user_gains,
        "followup_advice": record.followup_advice,
        "duration_min": record.duration_min,
        "created_at": to_iso(record.created_at),
        "updated_at": to_iso(record.updated_at),
    }
