"""危机处理 SOP：检测建档 → 值班接管 → 跟进 → 结案 → 留痕。"""

import time as time_mod

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User


def unique_phone() -> str:
    return "132" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def register(client) -> dict:
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Test123456",
            "nickname": "危机测试",
            "privacyAgreed": True,
            "serviceAgreed": True,
        },
    )
    assert resp.status_code == 201
    return {
        "phone": phone,
        "headers": {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"},
    }


def cleanup(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


def test_crisis_detect_assign_followup_resolve(client, admin_headers):
    acc = register(client)
    try:
        # 1) 情绪日记命中危机关键词 → 建档 + 双端通知
        resp = client.post(
            "/api/v1/emotion-journals",
            headers=acc["headers"],
            json={"moodType": "DOWN", "content": "最近压力很大，我有点不想活了"},
        )
        assert resp.status_code == 201

        flags = client.get("/api/v1/admin/crisis-flags?status=OPEN&page=1&pageSize=10", headers=admin_headers)
        assert flags.status_code == 200
        open_flags = [f for f in flags.json()["data"]["items"] if f["source"] == "EMOTION_JOURNAL"]
        assert len(open_flags) >= 1
        crisis_id = open_flags[0]["id"]

        user_unread = client.get("/api/v1/notifications/unread-count", headers=acc["headers"]).json()["data"]["count"]
        assert user_unread >= 1
        admin_unread = client.get("/api/v1/notifications/unread-count", headers=admin_headers).json()["data"]["count"]
        assert admin_unread >= 1

        # 2) 10 分钟内同来源去重：再写一条不再新建
        client.post(
            "/api/v1/emotion-journals",
            headers=acc["headers"],
            json={"moodType": "DOWN", "content": "想死，但我试着撑住"},
        )
        flags2 = client.get("/api/v1/admin/crisis-flags?page=1&pageSize=20", headers=admin_headers).json()["data"]["items"]
        same_source = [f for f in flags2 if f["source"] == "EMOTION_JOURNAL" and f["user"]["id"] == open_flags[0]["user"]["id"]]
        assert len(same_source) == 1

        # 3) 值班接管 → 跟进 → 结案
        resp = client.post(f"/api/v1/admin/crisis-flags/{crisis_id}/assign", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "FOLLOWING"

        resp = client.post(
            f"/api/v1/admin/crisis-flags/{crisis_id}/follow-up",
            headers=admin_headers,
            json={"note": "已电话联系用户，情绪平稳，建议预约正式服务"},
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/api/v1/admin/crisis-flags/{crisis_id}/resolve",
            headers=admin_headers,
            json={"note": "已回访，用户状态稳定，结案"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "RESOLVED"

        detail = client.get(f"/api/v1/admin/crisis-flags/{crisis_id}", headers=admin_headers).json()["data"]
        actions = [f["action"] for f in detail["followUps"]]
        assert actions == ["DETECT", "ASSIGN", "FOLLOW_UP", "RESOLVE"]

        logs = client.get("/api/v1/admin/audit-logs?action=ADMIN_CRISIS_RESOLVE&page=1&pageSize=10", headers=admin_headers)
        assert logs.status_code == 200
        assert any(i["action"] == "ADMIN_CRISIS_RESOLVE" for i in logs.json()["data"]["items"])
    finally:
        cleanup(acc["phone"])
