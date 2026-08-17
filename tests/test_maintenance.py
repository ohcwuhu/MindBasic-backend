"""维护任务：导出过期清理 + 孤儿文件扫描。"""

import asyncio
import time as time_mod
import uuid
from datetime import timedelta

from sqlalchemy import select, update

from app.db.session import AsyncSessionLocal, SessionLocal
from app.models.compliance import DataExport
from app.models.user import User
from app.api.v1.files import UPLOAD_DIR
from app.services.data_export_service import cleanup_expired_exports
from app.services.maintenance_service import sweep_orphan_uploads
from app.utils.time import utcnow_naive


def unique_phone() -> str:
    return "135" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


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
