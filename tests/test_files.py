import base64
import time as time_mod

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_upload_requires_login(client):
    resp = client.post(
        "/api/v1/files",
        files={"file": ("a.png", PNG_BYTES, "image/png")},
        data={"usage": "credential"},
    )
    assert resp.status_code == 401


def test_upload_ok_and_invalid_type(client, auth_headers):
    resp = client.post(
        "/api/v1/files",
        headers=auth_headers,
        files={"file": ("cert.png", PNG_BYTES, "image/png")},
        data={"usage": "credential"},
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["url"].startswith("/api/v1/files/")
    assert data["url"].endswith("/content")
    assert data["isPrivate"] is False
    assert data["originalName"] == "cert.png"

    resp = client.post(
        "/api/v1/files",
        headers=auth_headers,
        files={"file": ("x.txt", b"hello", "text/plain")},
        data={"usage": "credential"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "FILE_TYPE_INVALID"


def test_private_file_access_control(client, auth_headers, admin_headers):
    resp = client.post(
        "/api/v1/files",
        headers=auth_headers,
        files={"file": ("idcard.jpg", PNG_BYTES, "image/jpeg")},
        data={"usage": "idcard"},
    )
    assert resp.status_code == 201
    url = resp.json()["data"]["url"]

    # 本人可访问
    resp = client.get(url, headers=auth_headers)
    assert resp.status_code == 200

    # 未登录 → 401
    resp = client.get(url)
    assert resp.status_code == 401

    # 其他普通用户 → 403
    phone = "137" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)
    reg = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "路人", "privacyAgreed": True},
    )
    other_headers = {"Authorization": f"Bearer {reg.json()['data']['accessToken']}"}
    try:
        resp = client.get(url, headers=other_headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "FORBIDDEN"
    finally:
        db = SessionLocal()
        try:
            user = db.scalar(select(User).where(User.phone == phone))
            if user is not None:
                db.delete(user)
                db.commit()
        finally:
            db.close()

    # 管理员可访问
    resp = client.get(url, headers=admin_headers)
    assert resp.status_code == 200


def test_public_general_file(client, auth_headers):
    resp = client.post(
        "/api/v1/files",
        headers=auth_headers,
        files={"file": ("cover.png", PNG_BYTES, "image/png")},
        data={"usage": "general"},
    )
    assert resp.status_code == 201
    url = resp.json()["data"]["url"]
    resp = client.get(url)
    assert resp.status_code == 200


def test_upload_cleans_file_when_db_fails(client, auth_headers, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.main import app
    from app.api.v1.files import UPLOAD_DIR

    before = {p.name for p in UPLOAD_DIR.glob("*.png")}
    real_commit = Session.commit
    calls = {"n": 0}

    def failing_commit(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", failing_commit)
    quiet_client = TestClient(app, raise_server_exceptions=False)
    resp = quiet_client.post(
        "/api/v1/files",
        headers=auth_headers,
        files={"file": ("orphan.png", PNG_BYTES, "image/png")},
        data={"usage": "credential"},
    )
    assert resp.status_code == 500
    after = {p.name for p in UPLOAD_DIR.glob("*.png")}
    assert after == before
