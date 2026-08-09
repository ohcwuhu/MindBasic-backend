"""认证模块端到端测试（TestClient，连真实 MySQL）。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ADMIN_PHONE = "13800138000"
ADMIN_PASSWORD = "Admin@123456"
TEST_PHONE = "13900001111"


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
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": TEST_PHONE,
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
            "phone": TEST_PHONE,
            "password": "Test123456",
            "nickname": "测试用户",
            "privacyAgreed": True,
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "PHONE_EXISTS"


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
