"""数据合规：导出 / 注销删除 / 审计日志。"""

import time as time_mod

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User


def unique_phone() -> str:
    return "131" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def register(client) -> dict:
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Test123456",
            "nickname": "合规用户",
            "privacyAgreed": True,
            "serviceAgreed": True,
        },
    )
    assert resp.status_code == 201
    return {
        "phone": phone,
        "token": resp.json()["data"]["accessToken"],
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


def test_data_export_flow(client, admin_headers):
    acc = register(client)
    try:
        created = client.post("/api/v1/users/me/data-export", headers=acc["headers"])
        assert created.status_code == 201
        export_id = created.json()["data"]["id"]
        assert created.json()["data"]["status"] == "READY"

        listed = client.get("/api/v1/users/me/data-exports", headers=acc["headers"]).json()["data"]
        assert listed["pagination"]["totalItems"] >= 1
        assert any(i["id"] == export_id for i in listed["items"])

        down = client.get(f"/api/v1/users/me/data-exports/{export_id}/download", headers=acc["headers"])
        assert down.status_code == 200
        body = down.json()
        assert "profile" in body and "conversations" in body and "wallet_transactions" in body
        assert body["profile"]["nickname"] == "合规用户"

        logs = client.get("/api/v1/admin/audit-logs?action=USER_DATA_EXPORT&page=1&pageSize=10", headers=admin_headers)
        assert logs.status_code == 200
        assert any(i["action"] == "USER_DATA_EXPORT" for i in logs.json()["data"]["items"])
    finally:
        cleanup(acc["phone"])


def test_delete_account_requires_password_and_purges(client, admin_headers):
    acc = register(client)
    try:
        resp = client.post(
            "/api/v1/users/me/delete",
            headers=acc["headers"],
            json={"password": "WrongPass123"},
        )
        assert resp.status_code == 401

        resp = client.post(
            "/api/v1/users/me/delete",
            headers=acc["headers"],
            json={"password": "Test123456"},
        )
        assert resp.status_code == 200

        # 删除后原 token 失效
        me = client.get("/api/v1/users/me", headers=acc["headers"])
        assert me.status_code == 401

        logs = client.get("/api/v1/admin/audit-logs?action=USER_DELETE&page=1&pageSize=10", headers=admin_headers)
        assert logs.status_code == 200
        assert any(i["action"] == "USER_DELETE" for i in logs.json()["data"]["items"])
    finally:
        cleanup(acc["phone"])
