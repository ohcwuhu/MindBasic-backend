from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.content import Banner


def test_home_aggregation_public(client):
    resp = client.get("/api/v1/home")
    assert resp.status_code == 200
    data = resp.json()["data"]

    entries = data["quickEntries"]
    assert [e["key"] for e in entries] == ["self_coaching", "emotion_journal", "coaches", "science"]
    assert isinstance(data["banners"], list)
    assert isinstance(data["featuredArticles"], list)
    assert isinstance(data["recommendedCoaches"], list)

    resp = client.get("/api/v1/home/banners")
    assert resp.status_code == 200
    assert isinstance(resp.json()["data"]["items"], list)


def test_home_banners_from_db(client):
    db = SessionLocal()
    banner = None
    try:
        banner = Banner(
            title="测试轮播",
            image_url="http://example.com/banner.png",
            link_type="NONE",
            sort_order=0,
            is_enabled=True,
        )
        db.add(banner)
        db.commit()
        db.refresh(banner)
    finally:
        db.close()

    try:
        resp = client.get("/api/v1/home")
        assert resp.status_code == 200
        titles = [b["title"] for b in resp.json()["data"]["banners"]]
        assert "测试轮播" in titles
    finally:
        db = SessionLocal()
        try:
            row = db.get(Banner, banner.id)
            if row is not None:
                db.delete(row)
                db.commit()
        finally:
            db.close()


def test_home_with_optional_auth(client, auth_headers):
    resp = client.get("/api/v1/home", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == "OK"
