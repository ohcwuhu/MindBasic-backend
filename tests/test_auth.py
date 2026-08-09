"""认证模块端到端测试（TestClient，连真实 MySQL）。"""

import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.user import User
from sqlalchemy import select

client = TestClient(app)

ADMIN_PHONE = "13800138000"
ADMIN_PASSWORD = "Admin@123456"

def unique_phone() -> str:
    return "139" + str(int(time.time() * 1000) % 100000000).zfill(8)


def unique_phone() -> str:
    return "139" + str(int(time.time()) % 100000000).zfill(8)


def delete_user_by_phone(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


def disable_user_by_phone(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            user.status = "DISABLED"
            db.commit()
    finally:
        db.close()


def test_admin_login_and_me():
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": ADMIN_PHONE, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "OK"
    assert body["data"]["user"]["role"] == "ADMIN"
    assert body["data"]["user"]["phone"] == "138****8000"

    token = body["data"]["accessToken"]
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == 1


def test_refresh_rotation():
    client.post(
        "/api/v1/auth/login",
        json={"phone": ADMIN_PHONE, "password": ADMIN_PASSWORD},
    )
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["data"]["accessToken"]


def test_register_flow_and_duplicate():
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Test123456",
            "nickname": "测试用户",
            "privacyAgreed": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["data"]["user"]["role"] == "USER"

    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Test123456",
            "nickname": "测试用户",
            "privacyAgreed": True,
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "PHONE_EXISTS"
    delete_user_by_phone(phone)


def test_error_cases():
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": ADMIN_PHONE, "password": "wrongpass"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "CREDENTIAL_INVALID"

    resp = client.get("/api/v1/users/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"

    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": "123", "password": "x", "nickname": "", "privacyAgreed": True},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"
    assert "errors" in resp.json()["data"]


def test_patch_profile_and_logout():
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": ADMIN_PHONE, "password": ADMIN_PASSWORD},
    )
    token = resp.json()["data"]["accessToken"]
    resp = client.patch(
        "/api/v1/users/me",
        json={"nickname": "管理员"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


def test_disabled_account_login_and_access():
    phone = unique_phone()
    client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "禁用测试", "privacyAgreed": True},
    )
    disable_user_by_phone(phone)

    resp = client.post("/api/v1/auth/login", json={"phone": phone, "password": "Test123456"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "ACCOUNT_DISABLED"
    delete_user_by_phone(phone)


def test_token_expired_and_invalid_sub():
    now = datetime.now(timezone.utc)

    expired = jwt.encode(
        {
            "sub": "1",
            "role": "ADMIN",
            "type": "access",
            "jti": "test-expired",
            "iat": now - timedelta(hours=3),
            "exp": now - timedelta(hours=2),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"

    bad_sub = jwt.encode(
        {
            "sub": "abc",
            "role": "USER",
            "type": "access",
            "jti": "test-bad-sub",
            "iat": now,
            "exp": now + timedelta(minutes=30),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    resp = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {bad_sub}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


def test_register_requires_privacy_agreement():
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": unique_phone(), "password": "Test123456", "nickname": "未同意", "privacyAgreed": False},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_change_password_and_deactivate():
    phone = unique_phone()
    reg = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "OldPass123", "nickname": "资料测试", "privacyAgreed": True},
    )
    assert reg.status_code == 201
    user_id = reg.json()["data"]["user"]["id"]
    token = reg.json()["data"]["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}

    # 原密码错误
    resp = client.post(
        "/api/v1/users/me/password",
        headers=headers,
        json={"oldPassword": "Wrong123", "newPassword": "NewPass123"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "CREDENTIAL_INVALID"

    # 修改成功
    resp = client.post(
        "/api/v1/users/me/password",
        headers=headers,
        json={"oldPassword": "OldPass123", "newPassword": "NewPass123"},
    )
    assert resp.status_code == 200
    assert client.post("/api/v1/auth/login", json={"phone": phone, "password": "OldPass123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"phone": phone, "password": "NewPass123"}).status_code == 200

    # 注销后不可登录，手机号被释放可重新注册
    resp = client.post("/api/v1/users/me/deactivate", headers=headers)
    assert resp.status_code == 200
    assert client.post("/api/v1/auth/login", json={"phone": phone, "password": "NewPass123"}).status_code == 401
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "NewPass123", "nickname": "再来", "privacyAgreed": True},
    )
    assert resp.status_code == 201

    delete_user_by_phone(phone)
    db = SessionLocal()
    try:
        deleted = db.scalar(select(User).where(User.phone == f"del_{user_id}"))
        if deleted is not None:
            db.delete(deleted)
            db.commit()
    finally:
        db.close()


def test_login_rate_limit():
    for _ in range(10):
        resp = client.post(
            "/api/v1/auth/login",
            json={"phone": "13900000002", "password": "wrongpass"},
        )
        assert resp.status_code == 401
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": "13900000002", "password": "wrongpass"},
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"
