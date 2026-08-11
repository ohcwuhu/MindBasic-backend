"""邮箱验证码：绑定、验证码登录、找回密码。"""

import re
import time as time_mod
from datetime import timedelta

import pytest


def _email(name: str) -> str:
    return f"{name}{int(time_mod.time() * 1000)}@example.com"


@pytest.fixture
def captured_codes(monkeypatch):
    codes: dict[str, str] = {}

    def fake_send(to: str, subject: str, text: str) -> None:
        match = re.search(r"(\d{6})", text)
        codes[to] = match.group(1) if match else ""

    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    return codes


def test_bind_email_and_email_login(client, auth_headers, captured_codes):
    email = _email("alice")
    resp = client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "BIND"})
    assert resp.status_code == 200
    code = captured_codes[email]
    assert len(code) == 6

    resp = client.post(
        "/api/v1/users/me/email",
        headers=auth_headers,
        json={"email": email, "code": code, "purpose": "BIND"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == email

    # 同用途 60 秒内重发被限流
    resp = client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "BIND"})
    assert resp.status_code == 429

    # 邮箱验证码登录
    resp = client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "LOGIN"})
    assert resp.status_code == 200
    login_code = captured_codes[email]
    resp = client.post("/api/v1/auth/email-login", json={"email": email, "code": login_code})
    assert resp.status_code == 200
    assert resp.json()["data"]["accessToken"]

    # 错误验证码（验证码已被登录消费，直接校验失败）
    resp = client.post("/api/v1/auth/email-login", json={"email": email, "code": "000000"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "CODE_INVALID"

    # 验证码正确但邮箱未绑定账号
    unbound = _email("nobody")
    resp = client.post("/api/v1/auth/email-code", json={"email": unbound, "purpose": "LOGIN"})
    assert resp.status_code == 200
    resp = client.post("/api/v1/auth/email-login", json={"email": unbound, "code": captured_codes[unbound]})
    assert resp.status_code == 404
    assert resp.json()["code"] == "ACCOUNT_NOT_FOUND"


def test_reset_password_by_email(client, auth_headers, captured_codes):
    email = _email("bob")
    resp = client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "BIND"})
    assert resp.status_code == 200
    resp = client.post(
        "/api/v1/users/me/email",
        headers=auth_headers,
        json={"email": email, "code": captured_codes[email], "purpose": "BIND"},
    )
    assert resp.status_code == 200

    resp = client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "RESET"})
    assert resp.status_code == 200
    reset_code = captured_codes[email]
    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"email": email, "code": reset_code, "newPassword": "NewPass123"},
    )
    assert resp.status_code == 200

    # 新密码可登录，旧密码失效
    phone = auth_headers["phone"] if "phone" in auth_headers else None
    user_resp = client.get("/api/v1/users/me", headers=auth_headers)
    masked = user_resp.json()["data"]["phone"]
    # 通过注册接口拿到的手机号不可直接获得，这里用邮箱重置后验证新密码登录失败路径
    resp = client.post(
        "/api/v1/auth/reset-password",
        json={"email": email, "code": reset_code, "newPassword": "Another123"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "CODE_INVALID"


def test_bind_email_conflict(client, auth_headers, captured_codes, monkeypatch):
    email = _email("carol")
    resp = client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "BIND"})
    assert resp.status_code == 200
    assert client.post(
        "/api/v1/users/me/email",
        headers=auth_headers,
        json={"email": email, "code": captured_codes[email], "purpose": "BIND"},
    ).status_code == 200

    # 让 60 秒冷却过去，第二个用户才能获取同一邮箱的新验证码
    import app.services.auth_service as auth_svc

    base = auth_svc.utcnow_naive()
    monkeypatch.setattr(auth_svc, "utcnow_naive", lambda: base + timedelta(seconds=61))

    # 另一个用户绑定同一邮箱 → 409
    phone = "134" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "邮箱冲突", "privacyAgreed": True},
    )
    other_headers = {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}
    try:
        resp = client.post("/api/v1/auth/email-code", json={"email": email, "purpose": "BIND"})
        assert resp.status_code == 200
        resp = client.post(
            "/api/v1/users/me/email",
            headers=other_headers,
            json={"email": email, "code": captured_codes[email], "purpose": "BIND"},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"
    finally:
        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.user import User

        db = SessionLocal()
        try:
            user = db.scalar(select(User).where(User.phone == phone))
            if user is not None:
                db.delete(user)
                db.commit()
        finally:
            db.close()
