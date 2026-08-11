"""管理后台用户列表（注册时间筛选）。"""

import time

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User


def unique_phone() -> str:
    return "137" + str(int(time.time() * 1000) % 100000000).zfill(8)


def test_admin_users_filter_by_created_at(client, admin_headers):
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "时间筛选", "privacyAgreed": True},
    )
    assert resp.status_code == 201
    try:
        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        resp = client.get(
            f"/api/v1/admin/users?createdFrom={today}&createdTo={today}&page=1&pageSize=50",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        phones = [item["phone"] for item in resp.json()["data"]["items"]]
        assert phone[:3] + "****" + phone[-4:] in phones

        resp = client.get(
            "/api/v1/admin/users?createdFrom=2030-01-01&createdTo=2030-12-31&page=1&pageSize=50",
            headers=admin_headers,
        )
        phones = [item["phone"] for item in resp.json()["data"]["items"]]
        assert phone[:3] + "****" + phone[-4:] not in phones

        resp = client.get("/api/v1/admin/users?createdFrom=2026/08/11", headers=admin_headers)
        assert resp.status_code == 400
    finally:
        db = SessionLocal()
        try:
            user = db.scalar(select(User).where(User.phone == phone))
            if user is not None:
                db.delete(user)
                db.commit()
        finally:
            db.close()
