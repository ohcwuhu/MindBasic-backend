import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import SessionLocal
from app.main import app
from app.models.user import User


def unique_phone() -> str:
    return "139" + str(int(time.time() * 1000) % 100000000).zfill(8)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers(client):
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "接口测试", "privacyAgreed": True},
    )
    assert resp.status_code == 201
    token = resp.json()["data"]["accessToken"]
    yield {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def admin_headers(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138000", "password": "Admin@123456"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}
