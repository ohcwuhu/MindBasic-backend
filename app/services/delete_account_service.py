"""注销删除（被遗忘权）：删除/匿名化个人数据，保留财务与履约记录。"""

import os
import secrets
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.files import UPLOAD_DIR
from app.models.chat import ChatConversation
from app.models.coach import CoachProfile
from app.models.community import (
    CommunityComment,
    CommunityLike,
    CommunityMember,
    CommunityPost,
)
from app.models.content import ArticleFavorite
from app.models.email_code import EmailVerificationCode
from app.models.file import FileUpload
from app.models.growth import CheckIn, EmotionJournal, SelfCoachingRecord, UserBadge
from app.models.notification import Notification
from app.models.user import RefreshToken, User
from app.models.v1_1 import Review, Wallet
from app.services.audit_service import record_audit
from app.utils.time import utcnow_naive


async def delete_account_data(db: AsyncSession, user: User, ip: str | None = None) -> None:
    """注销并删除/匿名化个人数据。财务与履约记录（预约/订单）保留，用户主体匿名化。"""
    user_id = user.id
    now = utcnow_naive()

    # 1) 上传文件：先删磁盘与记录
    file_rows = list(await db.scalars(select(FileUpload).where(FileUpload.user_id == user_id)))
    for row in file_rows:
        try:
            path = UPLOAD_DIR / row.file_id
            if path.name == row.file_id and path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            pass
    if file_rows:
        await db.execute(delete(FileUpload).where(FileUpload.user_id == user_id))

    # 2) 会话（消息级联删除）
    await db.execute(delete(ChatConversation).where(ChatConversation.user_id == user_id))

    # 3) 自助内容
    await db.execute(delete(EmotionJournal).where(EmotionJournal.user_id == user_id))
    await db.execute(delete(SelfCoachingRecord).where(SelfCoachingRecord.user_id == user_id))
    await db.execute(delete(CheckIn).where(CheckIn.user_id == user_id))
    await db.execute(delete(UserBadge).where(UserBadge.user_id == user_id))
    await db.execute(delete(ArticleFavorite).where(ArticleFavorite.user_id == user_id))

    # 4) 社群
    await db.execute(delete(CommunityMember).where(CommunityMember.user_id == user_id))
    await db.execute(delete(CommunityPost).where(CommunityPost.user_id == user_id))
    await db.execute(delete(CommunityComment).where(CommunityComment.user_id == user_id))
    await db.execute(delete(CommunityLike).where(CommunityLike.user_id == user_id))

    # 5) 评价与通知
    await db.execute(delete(Review).where(Review.user_id == user_id))
    await db.execute(delete(Notification).where(Notification.user_id == user_id))

    # 6) 钱包（流水级联删除）
    wallet = await db.scalar(select(Wallet).where(Wallet.user_id == user_id))
    if wallet is not None:
        await db.execute(delete(Wallet).where(Wallet.id == wallet.id))

    # 7) 认证相关
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
    if user.email:
        await db.execute(delete(EmailVerificationCode).where(EmailVerificationCode.email == user.email))

    # 8) 用户主体匿名化（保留预约/订单等履约与财务记录的外键引用）
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            status="DISABLED",
            deleted_at=now,
            phone=f"deleted_{user_id}",
            nickname="已注销用户",
            email=None,
            avatar_url=None,
            password_hash=secrets.token_hex(32),
        )
    )

    # 9) 教练资料（若为教练）匿名化，保留业务结构
    coach = await db.scalar(select(CoachProfile).where(CoachProfile.user_id == user_id))
    if coach is not None:
        coach.real_name = "已注销教练"
        coach.bio = None
        coach.training_exp = None
        coach.service_concept = None

    await record_audit(
        db,
        actor_user_id=user_id,
        actor_role="USER",
        action="USER_DELETE",
        target_type="USER",
        target_id=user_id,
        detail={"deleted_at": now.isoformat()},
        ip=ip,
    )
    await db.commit()
