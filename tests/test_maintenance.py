"""维护任务：导出过期清理 + 孤儿文件扫描 + 异常中断会话收尾。"""

import asyncio
import time as time_mod
import uuid
from datetime import timedelta

from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal, SessionLocal
from app.models.ai_conversation import AiConversation
from app.models.compliance import DataExport
from app.models.user import User
from app.api.v1.files import UPLOAD_DIR
from app.services.data_export_service import cleanup_expired_exports
from app.services.maintenance_service import (
    sweep_orphan_uploads,
    sweep_stale_coaching_sessions,
)
from app.utils.time import utcnow_naive


def unique_phone() -> str:
    return "135" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def test_database_session_uses_utc():
    db = SessionLocal()
    try:
        zone, now, utc_now = db.execute(
            text("SELECT @@session.time_zone, NOW(), UTC_TIMESTAMP()")
        ).one()
        assert zone == "+00:00"
        assert abs((now - utc_now).total_seconds()) < 1
    finally:
        db.close()


def test_cleanup_expired_exports(client):
    phone = unique_phone()
    try:
        reg = client.post(
            "/api/v1/auth/register",
            json={
                "phone": phone,
                "password": "Test123456",
                "nickname": "维护测试",
                "privacyAgreed": True,
                "serviceAgreed": True,
            },
        )
        assert reg.status_code == 201
        headers = {"Authorization": f"Bearer {reg.json()['data']['accessToken']}"}
        created = client.post("/api/v1/users/me/data-export", headers=headers)
        assert created.status_code == 201
        export_id = created.json()["data"]["id"]

        db = SessionLocal()
        try:
            db.execute(
                update(DataExport)
                .where(DataExport.id == export_id)
                .values(expires_at=utcnow_naive() - timedelta(days=1))
            )
            db.commit()
        finally:
            db.close()

        # 孤儿上传文件（无数据库记录，uuid 命名 + 白名单后缀）
        orphan = UPLOAD_DIR / f"{uuid.uuid4().hex}.jpg"
        orphan.write_bytes(b"orphan")
        try:
            async def _run() -> tuple[int, int]:
                async with AsyncSessionLocal() as db:
                    expired = await cleanup_expired_exports(db)
                    orphans = await sweep_orphan_uploads(db)
                    return expired, orphans

            expired, orphans = asyncio.run(_run())
            assert expired >= 1
            assert orphans >= 1
            assert not orphan.exists()
        finally:
            orphan.unlink(missing_ok=True)
    finally:
        db = SessionLocal()
        try:
            user = db.scalar(select(User).where(User.phone == phone))
            if user is not None:
                db.delete(user)
                db.commit()
        finally:
            db.close()


def test_sweep_stale_coaching_sessions(client):
    """长时间停留在 ACTIVE 的会话应被标记为 ABANDONED，正常结束的不受影响。"""
    phone = unique_phone()
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Test123456",
            "nickname": "会话收尾测试",
            "privacyAgreed": True,
            "serviceAgreed": True,
        },
    )
    assert reg.status_code == 201

    db = SessionLocal()
    try:
        user_id = db.scalar(select(User.id).where(User.phone == phone))
        db_now = db.scalar(select(func.now()))
        stale = AiConversation(
            user_id=user_id, client_session_id="stale-sid", status="ACTIVE",
            created_at=db_now - timedelta(days=2),
        )
        recent = AiConversation(
            user_id=user_id, client_session_id="recent-sid", status="ACTIVE",
            created_at=db_now,
        )
        finished = AiConversation(
            user_id=user_id, client_session_id="finished-sid", status="ENDED",
            created_at=db_now - timedelta(days=3),
        )
        db.add_all([stale, recent, finished])
        db.commit()
        stale_id, recent_id, finished_id = int(stale.id), int(recent.id), int(finished.id)
    finally:
        db.close()

    try:
        async def _run() -> int:
            async with AsyncSessionLocal() as session:
                return await sweep_stale_coaching_sessions(session, stale_hours=6)

        swept = asyncio.run(_run())
        assert swept >= 1

        db = SessionLocal()
        try:
            assert db.get(AiConversation, stale_id).status == "ABANDONED"
            assert db.get(AiConversation, stale_id).ended_at is not None
            assert db.get(AiConversation, recent_id).status == "ACTIVE"
            assert db.get(AiConversation, finished_id).status == "ENDED"
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            for session_id in (stale_id, recent_id, finished_id):
                row = db.get(AiConversation, session_id)
                if row is not None:
                    db.delete(row)
            user = db.scalar(select(User).where(User.phone == phone))
            if user is not None:
                db.delete(user)
            db.commit()
        finally:
            db.close()
