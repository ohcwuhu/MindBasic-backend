"""用户数据导出（被遗忘权）：打包个人数据为 JSON，私有下载，保留 7 天。"""

import json
import uuid
from datetime import timedelta
from pathlib import Path

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_conversation import AiConversation, AiMessage
from app.core.exceptions import AppError
from app.models.chat import ChatConversation, ChatMessage
from app.models.coach import Appointment, CoachProfile
from app.models.compliance import DataExport
from app.models.content import Article, ArticleFavorite
from app.models.growth import (
    CheckIn,
    EmotionJournal,
    GrowthAssessmentResult,
    GrowthAssessmentTemplate,
    SelfCoachingRecord,
    UserBadge,
)
from app.models.user import User
from app.models.v1_1 import Order, Review, Wallet, WalletTransaction
from app.services.appointment_service import my_appointments_to_out
from app.utils.format import mask_phone
from app.utils.time import to_iso, utcnow_naive

EXPORT_DIR = Path(__file__).resolve().parents[2] / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_TTL_DAYS = 7


async def _collect_export_data(db: AsyncSession, user: User) -> dict:
    def iso(dt) -> str | None:
        return to_iso(dt)

    # 预约
    appointments = list(
        await db.scalars(
            select(Appointment)
            .where(Appointment.user_id == user.id)
            .order_by(Appointment.created_at.desc())
        )
    )
    appointment_items = await my_appointments_to_out(db, appointments)

    # 订单与钱包流水
    orders = list(await db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc())))
    wallet = await db.scalar(select(Wallet).where(Wallet.user_id == user.id))
    wallet_txs = (
        list(
            await db.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == wallet.id)
                .order_by(WalletTransaction.created_at.desc())
            )
        )
        if wallet
        else []
    )

    # 情绪日记 / 自我教练记录 / 打卡 / 徽章
    journals = list(
        await db.scalars(
            select(EmotionJournal).where(EmotionJournal.user_id == user.id).order_by(EmotionJournal.created_at.desc())
        )
    )
    records = list(
        await db.scalars(
            select(SelfCoachingRecord).where(SelfCoachingRecord.user_id == user.id).order_by(SelfCoachingRecord.created_at.desc())
        )
    )
    checkins = list(
        await db.scalars(select(CheckIn).where(CheckIn.user_id == user.id).order_by(CheckIn.check_date.desc()))
    )
    badges = list(await db.scalars(select(UserBadge).where(UserBadge.user_id == user.id)))

    # 评价与收藏
    reviews = list(
        await db.scalars(select(Review).where(Review.user_id == user.id).order_by(Review.created_at.desc()))
    )
    fav_rows = list(
        await db.scalars(
            select(ArticleFavorite).where(ArticleFavorite.user_id == user.id).order_by(ArticleFavorite.created_at.desc())
        )
    )
    article_ids = [f.article_id for f in fav_rows]
    articles = {
        a.id: a
        for a in await db.scalars(select(Article).where(Article.id.in_(article_ids)))
    } if article_ids else {}

    # 用户—教练私信。教练本人也应导出自己可访问的会话，但不混入个案备注等内部资料。
    coach_profile_id = await db.scalar(
        select(CoachProfile.id).where(CoachProfile.user_id == user.id)
    )
    chat_filter = ChatConversation.user_id == user.id
    if coach_profile_id is not None:
        chat_filter = or_(chat_filter, ChatConversation.coach_id == coach_profile_id)
    convs = list(
        await db.scalars(
            select(ChatConversation)
            .where(chat_filter)
            .order_by(ChatConversation.created_at.desc())
        )
    )
    conv_ids = [conv.id for conv in convs]
    all_chat_messages = list(
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id.in_(conv_ids))
            .order_by(ChatMessage.created_at.asc())
        )
    ) if conv_ids else []
    chat_messages_by_conv: dict[int, list[ChatMessage]] = defaultdict(list)
    for message in all_chat_messages:
        chat_messages_by_conv[message.conversation_id].append(message)
    conversations: list[dict] = []
    for conv in convs:
        conversations.append({
            "conversation_id": conv.id,
            "created_at": iso(conv.created_at),
            "my_role": "COACH" if conv.coach_id == coach_profile_id else "USER",
            "messages": [
                {
                    "sender_role": m.sender_role,
                    "content": m.content,
                    "read_at": iso(m.read_at),
                    "created_at": iso(m.created_at),
                }
                for m in chat_messages_by_conv.get(conv.id, [])
            ],
        })

    # AI 自我教练会话与消息
    ai_convs = list(
        await db.scalars(
            select(AiConversation)
            .where(AiConversation.user_id == user.id)
            .order_by(AiConversation.created_at.desc())
        )
    )
    ai_conv_ids = [conv.id for conv in ai_convs]
    ai_messages = list(
        await db.scalars(
            select(AiMessage)
            .where(AiMessage.conversation_id.in_(ai_conv_ids))
            .order_by(AiMessage.turn_index.asc(), AiMessage.created_at.asc(), AiMessage.id.asc())
        )
    ) if ai_conv_ids else []
    ai_messages_by_conv: dict[int, list[AiMessage]] = defaultdict(list)
    for message in ai_messages:
        ai_messages_by_conv[message.conversation_id].append(message)
    ai_conversation_items = [
        {
            "id": conv.id,
            "title": conv.title,
            "status": conv.status,
            "message_count": conv.message_count,
            "turn_count": conv.turn_count,
            "final_stage": conv.final_stage,
            "max_risk_level": conv.max_risk_level,
            "consent": conv.consent,
            "summary": conv.summary,
            "summary_confirmed_at": iso(conv.summary_confirmed_at),
            "journal_id": conv.journal_id,
            "created_at": iso(conv.created_at),
            "ended_at": iso(conv.ended_at),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "turn_index": m.turn_index,
                    "stage": m.stage,
                    "risk_level": m.risk_level,
                    "created_at": iso(m.created_at),
                }
                for m in ai_messages_by_conv.get(conv.id, [])
            ],
        }
        for conv in ai_convs
    ]

    assessment_rows = (
        await db.execute(
            select(
                GrowthAssessmentResult,
                GrowthAssessmentTemplate.name,
                GrowthAssessmentTemplate.version,
            )
            .join(
                GrowthAssessmentTemplate,
                GrowthAssessmentTemplate.id == GrowthAssessmentResult.template_id,
            )
            .where(GrowthAssessmentResult.user_id == user.id)
            .order_by(GrowthAssessmentResult.created_at.desc())
        )
    ).all()

    return {
        "exported_at": iso(utcnow_naive()),
        "profile": {
            "nickname": user.nickname,
            "phone": mask_phone(user.phone),
            "email": user.email,
            "gender": user.gender,
            "role": user.role,
            "created_at": iso(user.created_at),
        },
        "appointments": appointment_items,
        "orders": [
            {
                "order_no": o.order_no,
                "type": o.type,
                "status": o.status,
                "amount_in_cents": o.amount_in_cents,
                "created_at": iso(o.created_at),
                "paid_at": iso(o.paid_at),
            }
            for o in orders
        ],
        "wallet_balance_in_cents": wallet.balance_in_cents if wallet else 0,
        "wallet_transactions": [
            {
                "change_in_cents": t.change_in_cents,
                "balance_after": t.balance_after,
                "biz_type": t.biz_type,
                "note": t.note,
                "created_at": iso(t.created_at),
            }
            for t in wallet_txs
        ],
        "emotion_journals": [
            {"mood_type": j.mood_type, "content": j.content, "feedback": j.feedback, "created_at": iso(j.created_at)}
            for j in journals
        ],
        "self_coaching_records": [
            {
                "template_id": r.template_id,
                "status": r.status,
                "answers": r.answers,
                "action_card": r.action_card,
                "created_at": iso(r.created_at),
            }
            for r in records
        ],
        "check_ins": [
            {"check_date": c.check_date.isoformat(), "content": c.content, "created_at": iso(c.created_at)}
            for c in checkins
        ],
        "badges": [{"badge_id": b.badge_id, "earned_at": iso(b.earned_at)} for b in badges],
        "reviews": [
            {"appointment_id": r.appointment_id, "rating": r.rating, "content": r.content, "created_at": iso(r.created_at)}
            for r in reviews
        ],
        "favorites": [
            {"article_id": f.article_id, "title": articles[f.article_id].title if f.article_id in articles else None}
            for f in fav_rows
        ],
        "conversations": conversations,
        "ai_conversations": ai_conversation_items,
        "growth_assessments": [
            {
                "id": result.id,
                "template_id": result.template_id,
                "template_name": template_name,
                "template_version": template_version,
                "answers": result.answers,
                "scores": result.scores,
                "report": result.report,
                "created_at": iso(result.created_at),
            }
            for result, template_name, template_version in assessment_rows
        ],
    }


async def create_data_export(db: AsyncSession, user: User) -> DataExport:
    data = await _collect_export_data(db, user)
    file_id = f"{uuid.uuid4().hex}.json"
    (EXPORT_DIR / file_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    record = DataExport(
        user_id=user.id,
        status="READY",
        format="JSON",
        file_id=file_id,
        size=(EXPORT_DIR / file_id).stat().st_size,
        expires_at=utcnow_naive() + timedelta(days=EXPORT_TTL_DAYS),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_data_exports(db: AsyncSession, user_id: int, page: int, page_size: int) -> tuple[list[dict], int]:
    stmt = select(DataExport).where(DataExport.user_id == user_id)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        await db.scalars(
            stmt.order_by(DataExport.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = [
        {
            "id": r.id,
            "status": r.status,
            "format": r.format,
            "size": r.size,
            "expires_at": to_iso(r.expires_at),
            "created_at": to_iso(r.created_at),
        }
        for r in rows
    ]
    return items, total


async def get_export_or_404(db: AsyncSession, user_id: int, export_id: int) -> DataExport:
    record = await db.scalar(
        select(DataExport).where(DataExport.id == export_id, DataExport.user_id == user_id)
    )
    if record is None:
        raise AppError(404, "NOT_FOUND", "导出记录不存在")
    if record.status != "READY":
        raise AppError(409, "EXPORT_NOT_READY", "导出文件不可用")
    if record.expires_at is not None and record.expires_at < utcnow_naive():
        raise AppError(410, "EXPORT_EXPIRED", "导出文件已过期，请重新导出")
    return record


def export_path(record: DataExport) -> Path:
    path = EXPORT_DIR / record.file_id
    if path.name != record.file_id or not path.is_file():
        raise AppError(404, "NOT_FOUND", "导出文件不存在")
    return path


async def cleanup_expired_exports(db: AsyncSession) -> int:
    """清理已过期的导出：删除磁盘文件并标记 EXPIRED（定时任务调用）。"""
    now = utcnow_naive()
    rows = list(
        await db.scalars(
            select(DataExport).where(
                DataExport.status == "READY",
                DataExport.expires_at.is_not(None),
                DataExport.expires_at < now,
            )
        )
    )
    for record in rows:
        path = EXPORT_DIR / record.file_id
        if path.name == record.file_id and path.is_file():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        record.status = "EXPIRED"
    await db.commit()
    return len(rows)
