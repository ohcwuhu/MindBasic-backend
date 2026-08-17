import time as time_mod

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User


def unique_phone() -> str:
    return "137" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def delete_user_by_phone(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


def test_admin_users_manage(client, admin_headers):
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "待管理用户", "privacyAgreed": True, "serviceAgreed": True},
    )
    assert resp.status_code == 201
    user_id = resp.json()["data"]["user"]["id"]
    token = resp.json()["data"]["accessToken"]
    user_headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = client.get("/api/v1/admin/users?keyword=待管理&page=1&pageSize=10", headers=admin_headers)
        assert resp.status_code == 200
        item = next(u for u in resp.json()["data"]["items"] if u["id"] == user_id)
        assert item["phone"].startswith("137") and "****" in item["phone"]

        resp = client.patch(
            f"/api/v1/admin/users/{user_id}/status",
            headers=admin_headers,
            json={"status": "DISABLED"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["isDisabled"] is True

        resp = client.post("/api/v1/auth/login", json={"phone": phone, "password": "Test123456"})
        assert resp.status_code == 403
        assert resp.json()["code"] == "ACCOUNT_DISABLED"

        resp = client.patch(
            f"/api/v1/admin/users/{user_id}/status",
            headers=admin_headers,
            json={"status": "ENABLED"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["isDisabled"] is False

        resp = client.patch(
            "/api/v1/admin/users/1/status",
            headers=admin_headers,
            json={"status": "DISABLED"},
        )
        assert resp.status_code == 409  # 不能禁用自己
    finally:
        delete_user_by_phone(phone)


def test_admin_articles_banned_words_and_crud(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/articles",
        headers=admin_headers,
        json={
            "title": "如何陪孩子平稳面对考试",
            "summary": "考前陪伴",
            "content": "<p>用资源视角陪伴孩子</p>",
            "categoryId": 1,
            "isPinned": True,
            "status": "PUBLISHED",
        },
    )
    assert resp.status_code == 201
    article_id = resp.json()["data"]["id"]
    assert resp.json()["data"]["publishedAt"] is not None

    resp = client.post(
        "/api/v1/admin/articles",
        headers=admin_headers,
        json={"title": "焦虑的治愈方法", "content": "x", "status": "PUBLISHED"},
    )
    assert resp.status_code == 400
    assert "禁用词" in resp.json()["message"]

    resp = client.patch(
        f"/api/v1/admin/articles/{article_id}",
        headers=admin_headers,
        json={"isPinned": False},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["isPinned"] is False

    resp = client.get("/api/v1/admin/articles?status=PUBLISHED&page=1&pageSize=10", headers=admin_headers)
    assert any(a["id"] == article_id for a in resp.json()["data"]["items"])

    resp = client.delete(f"/api/v1/admin/articles/{article_id}", headers=admin_headers)
    assert resp.status_code == 204
    resp = client.get("/api/v1/articles?page=1&pageSize=10")
    assert article_id not in [a["id"] for a in resp.json()["data"]["items"]]


def test_admin_categories_crud(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/article-categories",
        headers=admin_headers,
        json={"name": "测试分类", "sortOrder": 9},
    )
    assert resp.status_code == 201
    category_id = resp.json()["data"]["id"]

    resp = client.post(
        "/api/v1/admin/article-categories",
        headers=admin_headers,
        json={"name": "测试分类"},
    )
    assert resp.status_code == 409

    resp = client.patch(
        f"/api/v1/admin/article-categories/{category_id}",
        headers=admin_headers,
        json={"isEnabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["isEnabled"] is False

    resp = client.delete(f"/api/v1/admin/article-categories/{category_id}", headers=admin_headers)
    assert resp.status_code == 204


def test_admin_banners_crud(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/banners",
        headers=admin_headers,
        json={"title": "考研季陪伴", "imageUrl": "http://example.com/b1.png", "sortOrder": 1},
    )
    assert resp.status_code == 201
    banner_id = resp.json()["data"]["id"]

    resp = client.patch(
        f"/api/v1/admin/banners/{banner_id}",
        headers=admin_headers,
        json={"isEnabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["isEnabled"] is False

    resp = client.get("/api/v1/home/banners")
    assert banner_id not in [b["id"] for b in resp.json()["data"]["items"]]

    resp = client.delete(f"/api/v1/admin/banners/{banner_id}", headers=admin_headers)
    assert resp.status_code == 204


def test_admin_tags_crud(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/tags",
        headers=admin_headers,
        json={"name": "婚姻关系", "type": "FIELD", "sortOrder": 99},
    )
    assert resp.status_code == 201
    tag_id = resp.json()["data"]["id"]

    resp = client.post(
        "/api/v1/admin/tags",
        headers=admin_headers,
        json={"name": "婚姻关系", "type": "FIELD"},
    )
    assert resp.status_code == 409

    resp = client.delete(f"/api/v1/admin/tags/{tag_id}", headers=admin_headers)
    assert resp.status_code == 204


def test_admin_feedback_crud(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/feedback-lib",
        headers=admin_headers,
        json={"moodType": "CALM", "content": "平稳的你是最有力量的。", "sortOrder": 9},
    )
    assert resp.status_code == 201
    item_id = resp.json()["data"]["id"]

    resp = client.get("/api/v1/admin/feedback-lib?moodType=CALM", headers=admin_headers)
    assert any(f["id"] == item_id for f in resp.json()["data"]["items"])

    resp = client.patch(
        f"/api/v1/admin/feedback-lib/{item_id}",
        headers=admin_headers,
        json={"isEnabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["isEnabled"] is False

    resp = client.delete(f"/api/v1/admin/feedback-lib/{item_id}", headers=admin_headers)
    assert resp.status_code == 204


def test_admin_stats(client, admin_headers):
    resp = client.get("/api/v1/admin/stats", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    for key in ("userCount", "coachCount", "approvedCoachCount", "appointmentCount",
                "pendingAppointmentCount", "articleCount", "todayUserCount", "todayAppointmentCount"):
        assert key in data


def test_article_content_sanitized(client, admin_headers):
    resp = client.post(
        "/api/v1/admin/articles",
        headers=admin_headers,
        json={
            "title": "XSS 测试文章",
            "content": "<p>安全内容</p><script>alert(1)</script><img src=x onerror=alert(1)>",
            "status": "PUBLISHED",
        },
    )
    assert resp.status_code == 201
    content = resp.json()["data"]["content"]
    assert "<script" not in content
    assert "onerror" not in content
    assert "<p>安全内容</p>" in content
    article_id = resp.json()["data"]["id"]

    resp = client.get(f"/api/v1/articles/{article_id}")
    assert resp.status_code == 200
    public_content = resp.json()["data"]["content"]
    assert "<script" not in public_content
    assert "onerror" not in public_content
    assert "<p>安全内容</p>" in public_content

    resp = client.delete(f"/api/v1/admin/articles/{article_id}", headers=admin_headers)
    assert resp.status_code == 204


def test_admin_requires_admin_role(client):
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "普通用户", "privacyAgreed": True, "serviceAgreed": True},
    )
    headers = {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}
    try:
        resp = client.get("/api/v1/admin/users", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "FORBIDDEN"
    finally:
        delete_user_by_phone(phone)
