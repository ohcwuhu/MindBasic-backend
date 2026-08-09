from datetime import timedelta

import pytest

from app.db.session import SessionLocal
from app.models.content import Article
from app.utils.time import utcnow_naive


@pytest.fixture(scope="module")
def articles(client):
    db = SessionLocal()
    objs = []
    try:
        base = utcnow_naive()
        a1 = Article(
            title="考前心态调整三招",
            summary="考前焦虑的实用方法",
            content="<p>正文：呼吸练习与正面自我对话</p>",
            category_id=1,
            is_pinned=True,
            status="PUBLISHED",
            view_count=0,
            published_at=base,
        )
        a2 = Article(
            title="一位教练的真实故事",
            summary="教练如何陪伴考生家庭",
            content="<p>教练故事正文</p>",
            category_id=2,
            status="PUBLISHED",
            view_count=0,
            published_at=base + timedelta(seconds=1),
        )
        a3 = Article(
            title="为什么孩子越来越不爱说话",
            summary="亲子沟通常见困惑",
            content="<p>困惑与资源视角</p>",
            category_id=3,
            status="PUBLISHED",
            view_count=0,
            published_at=base + timedelta(seconds=2),
        )
        draft = Article(title="未发布的草稿", content="<p>x</p>", status="DRAFT")
        db.add_all([a1, a2, a3, draft])
        db.commit()
        for obj in (a1, a2, a3, draft):
            db.refresh(obj)
        objs = [a1, a2, a3, draft]
        yield {"a1": a1.id, "a2": a2.id, "a3": a3.id, "draft": draft.id}
    finally:
        if objs:
            db = SessionLocal()
            try:
                for obj in objs:
                    db.delete(db.get(Article, obj.id))
                db.commit()
            finally:
                db.close()
        else:
            db.close()


def test_categories(client):
    resp = client.get("/api/v1/article-categories")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["data"]["items"]]
    assert names == ["成长技巧", "教练真实故事", "常见成长困惑"]


def test_article_list_and_filter(client, articles):
    resp = client.get("/api/v1/articles?page=1&pageSize=10")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["pagination"]["totalItems"] == 3
    assert data["items"][0]["title"] == "考前心态调整三招"  # 置顶优先

    resp = client.get("/api/v1/articles?categoryId=2")
    ids = [a["id"] for a in resp.json()["data"]["items"]]
    assert ids == [articles["a2"]]

    resp = client.get("/api/v1/articles?keyword=孩子")
    assert resp.json()["data"]["pagination"]["totalItems"] == 1


def test_article_detail_and_view_count(client, articles):
    resp = client.get(f"/api/v1/articles/{articles['a1']}")
    assert resp.status_code == 200
    first = resp.json()["data"]
    assert first["content"].startswith("<p>")
    first_views = first["viewCount"]

    resp = client.get(f"/api/v1/articles/{articles['a1']}")
    second_views = resp.json()["data"]["viewCount"]
    assert second_views == first_views + 1

    resp = client.get(f"/api/v1/articles/{articles['draft']}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ARTICLE_NOT_FOUND"


def test_favorite_toggle_and_my_favorites(client, auth_headers, articles):
    resp = client.post(f"/api/v1/articles/{articles['a1']}/favorite", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["isFavorite"] is True

    resp = client.get("/api/v1/users/me/favorites", headers=auth_headers)
    ids = [a["id"] for a in resp.json()["data"]["items"]]
    assert articles["a1"] in ids
    assert all(a["isFavorite"] for a in resp.json()["data"]["items"])

    resp = client.get("/api/v1/articles?page=1&pageSize=10", headers=auth_headers)
    mine = next(a for a in resp.json()["data"]["items"] if a["id"] == articles["a1"])
    assert mine["isFavorite"] is True

    resp = client.get("/api/v1/articles?page=1&pageSize=10")
    anon = next(a for a in resp.json()["data"]["items"] if a["id"] == articles["a1"])
    assert anon["isFavorite"] is False

    resp = client.post(f"/api/v1/articles/{articles['a1']}/favorite", headers=auth_headers)
    assert resp.json()["data"]["isFavorite"] is False

    resp = client.post(f"/api/v1/articles/{articles['a1']}/favorite")
    assert resp.status_code == 401


def test_favorite_draft_article_404(client, auth_headers, articles):
    resp = client.post(f"/api/v1/articles/{articles['draft']}/favorite", headers=auth_headers)
    assert resp.status_code == 404
