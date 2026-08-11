"""主题社群：加入/退出、发帖、评论、点赞、教练治理、管理员上下架。"""

import time

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User


def unique_phone(prefix: str = "135") -> str:
    return prefix + str(int(time.time() * 1000) % 100000000).zfill(8)


def delete_user_by_phone(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def coach_env(client, admin_headers):
    """已审核通过的教练（用于创建/管理社群）。"""
    phone = unique_phone("135")
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "社群教练", "privacyAgreed": True},
    )
    assert resp.status_code == 201
    coach_headers = {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}
    body = {
        "realName": "社群教练",
        "bio": "专注成长陪伴",
        "trainingExp": "心理教练认证",
        "serviceConcept": "赋能陪伴",
        "yearsOfExperience": 2,
        "credentialUrls": [],
        "idCardUrl": None,
        "tagIds": [1],
        "services": [{"name": "单次", "serviceType": "SINGLE", "durationMin": 60, "priceInCents": 9900}],
    }
    assert client.post("/api/v1/coach/profile", headers=coach_headers, json=body).status_code == 201
    audits = client.get(
        "/api/v1/admin/coach-audits?status=PENDING&page=1&pageSize=20",
        headers=admin_headers,
    ).json()["data"]["items"]
    mine = next(a for a in audits if a["coachName"] == "社群教练")
    assert client.post(f"/api/v1/admin/coach-audits/{mine['id']}/approve", headers=admin_headers).status_code == 200
    yield {"headers": coach_headers}
    delete_user_by_phone(phone)


def test_community_public_and_join_flow(client, auth_headers, admin_headers, coach_env):
    # 公开列表/详情
    resp = client.get("/api/v1/communities?page=1&pageSize=10")
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["data"]["items"]]
    assert "高考家长互助群" in names

    # 创建（教练，唯一名称避免重复运行残留）
    community_name = f"考前陪伴实验群{int(time.time() * 1000) % 100000}"
    resp = client.post(
        "/api/v1/communities",
        headers=coach_env["headers"],
        json={"name": community_name, "description": "一起平稳走过备考期"},
    )
    assert resp.status_code == 201
    community_id = resp.json()["data"]["id"]
    assert resp.json()["data"]["memberCount"] == 1
    assert resp.json()["data"]["canManage"] is True

    # 普通用户创建被拒
    resp = client.post(
        "/api/v1/communities",
        headers=auth_headers,
        json={"name": "普通用户群", "description": "x"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "COACH_NOT_APPROVED"

    # 重复名称
    resp = client.post(
        "/api/v1/communities",
        headers=coach_env["headers"],
        json={"name": community_name, "description": "x"},
    )
    assert resp.status_code == 409

    # 用户加入
    resp = client.post(f"/api/v1/communities/{community_id}/join", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["joined"] is True
    resp = client.post(f"/api/v1/communities/{community_id}/join", headers=auth_headers)
    assert resp.status_code == 409

    # 发帖 / 评论 / 点赞
    resp = client.post(
        f"/api/v1/communities/{community_id}/posts",
        headers=auth_headers,
        json={"content": "第一次在群里打卡，今天完成了一件小事"},
    )
    assert resp.status_code == 201
    post_id = resp.json()["data"]["id"]

    resp = client.post(
        f"/api/v1/communities/{community_id}/posts/{post_id}/comments",
        headers=coach_env["headers"],
        json={"content": "欢迎你，小行动就是起点"},
    )
    assert resp.status_code == 201
    comment_id = resp.json()["data"]["id"]

    resp = client.post(f"/api/v1/communities/{community_id}/posts/{post_id}/like", headers=auth_headers)
    assert resp.json()["data"] == {"liked": True, "like_count": 1}
    resp = client.post(f"/api/v1/communities/{community_id}/posts/{post_id}/like", headers=auth_headers)
    assert resp.json()["data"] == {"liked": False, "like_count": 0}

    # 教练置顶 + 列表置顶在前
    resp = client.patch(
        f"/api/v1/communities/{community_id}/posts/{post_id}/pin",
        headers=coach_env["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["isPinned"] is True
    resp = client.get(f"/api/v1/communities/{community_id}/posts?page=1&pageSize=10", headers=auth_headers)
    assert resp.json()["data"]["items"][0]["id"] == post_id

    # 详情含评论
    resp = client.get(
        f"/api/v1/communities/{community_id}/posts/{post_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["comments"][0]["id"] == comment_id

    # 禁用词
    resp = client.post(
        f"/api/v1/communities/{community_id}/posts",
        headers=auth_headers,
        json={"content": "想找治疗的方法"},
    )
    assert resp.status_code == 400

    # 教练删除评论、删除成员帖子
    resp = client.delete(
        f"/api/v1/communities/{community_id}/posts/{post_id}/comments/{comment_id}",
        headers=coach_env["headers"],
    )
    assert resp.status_code == 204
    resp = client.delete(
        f"/api/v1/communities/{community_id}/posts/{post_id}",
        headers=coach_env["headers"],
    )
    assert resp.status_code == 204

    # 退出后无法看帖
    resp = client.post(f"/api/v1/communities/{community_id}/leave", headers=auth_headers)
    assert resp.status_code == 200
    resp = client.get(f"/api/v1/communities/{community_id}/posts", headers=auth_headers)
    assert resp.status_code == 403

    # 我的社群
    resp = client.get("/api/v1/communities/mine", headers=coach_env["headers"])
    assert any(item["id"] == community_id for item in resp.json()["data"]["items"])

    # 管理员下架
    resp = client.patch(
        f"/api/v1/admin/communities/{community_id}/status",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert resp.status_code == 200
    resp = client.get(f"/api/v1/communities/{community_id}")
    assert resp.status_code == 404
