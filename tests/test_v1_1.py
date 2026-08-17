import time as time_mod
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.coach import Appointment, CoachProfile, CoachSlot, Service
from app.models.user import User
from app.models.v1_1 import Order, Payment, Refund


def unique_phone(prefix: str) -> str:
    return prefix + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


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
def env(client, admin_headers):
    coach_phone = unique_phone("136")
    user_phone = unique_phone("135")

    # 教练：注册 → 提交资料 → 管理员通过
    reg = client.post(
        "/api/v1/auth/register",
        json={"phone": coach_phone, "password": "Test123456", "nickname": "V11教练", "privacyAgreed": True},
    )
    assert reg.status_code == 201
    coach_headers = {"Authorization": f"Bearer {reg.json()['data']['accessToken']}"}
    body = {
        "realName": "V11教练",
        "bio": "测试",
        "trainingExp": "测试",
        "serviceConcept": "赋能",
        "yearsOfExperience": 2,
        "credentialUrls": [],
        "idCardUrl": None,
        "tagIds": [1],
        "services": [{"name": "单次咨询", "serviceType": "SINGLE", "durationMin": 60, "priceInCents": 9900}],
    }
    assert client.post("/api/v1/coach/profile", headers=coach_headers, json=body).status_code == 201
    audits = client.get(
        "/api/v1/admin/coach-audits?status=PENDING&page=1&pageSize=10", headers=admin_headers
    ).json()["data"]["items"]
    mine = next(a for a in audits if a["coachName"] == "V11教练")
    assert client.post(f"/api/v1/admin/coach-audits/{mine['id']}/approve", headers=admin_headers).status_code == 200
    profile = client.get("/api/v1/coach/profile", headers=coach_headers).json()["data"]
    coach_id = profile["id"]
    service_id = profile["services"][0]["id"]

    db = SessionLocal()
    slot_id = None
    try:
        slot = CoachSlot(
            coach_id=coach_id,
            date=date.today() + timedelta(days=2),
            start_time=time(9, 0),
            end_time=time(10, 0),
            status="AVAILABLE",
        )
        db.add(slot)
        db.commit()
        db.refresh(slot)
        slot_id = slot.id
    finally:
        db.close()

    # 用户注册并预约 → 教练确认 → 完成
    reg = client.post(
        "/api/v1/auth/register",
        json={"phone": user_phone, "password": "Test123456", "nickname": "评价用户", "privacyAgreed": True},
    )
    user_headers = {"Authorization": f"Bearer {reg.json()['data']['accessToken']}"}
    booking = client.post(
        "/api/v1/appointments",
        headers=user_headers,
        json={"coachId": coach_id, "serviceId": service_id, "slotId": slot_id, "needDesc": "测试需求"},
    )
    assert booking.status_code == 201
    appointment_id = booking.json()["data"]["id"]
    order_no = booking.json()["data"]["orderNo"]
    assert client.post(
        f"/api/v1/orders/{order_no}/pay", headers=user_headers, json={"method": "MOCK"}
    ).status_code == 200
    assert client.post(f"/api/v1/coach/appointments/{appointment_id}/confirm", headers=coach_headers).status_code == 200
    assert client.post(f"/api/v1/coach/appointments/{appointment_id}/complete", headers=coach_headers).status_code == 200

    yield {
        "coach_headers": coach_headers,
        "user_headers": user_headers,
        "appointment_id": appointment_id,
        "coach_id": coach_id,
        "coach_phone": coach_phone,
        "user_phone": user_phone,
    }

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == user_phone))
        if user is not None:
            order_ids = list(db.scalars(select(Order.id).where(Order.user_id == user.id)))
            if order_ids:
                db.execute(Payment.__table__.delete().where(Payment.order_id.in_(order_ids)))
                db.execute(Refund.__table__.delete().where(Refund.order_id.in_(order_ids)))
                db.execute(Order.__table__.delete().where(Order.id.in_(order_ids)))
        db.execute(Appointment.__table__.delete().where(Appointment.coach_id == coach_id))
        db.commit()
    finally:
        db.close()
    delete_user_by_phone(coach_phone)
    delete_user_by_phone(user_phone)


def test_review_flow(client, env):
    headers = env["user_headers"]
    mine = client.get("/api/v1/appointments/mine?page=1&pageSize=20", headers=headers).json()["data"]["items"]
    target = next(a for a in mine if a["id"] == env["appointment_id"])
    assert target["reviewed"] is False

    resp = client.post(
        f"/api/v1/appointments/{env['appointment_id']}/review",
        headers=headers,
        json={"rating": 5, "content": "很专业，收获很大"},
    )
    assert resp.status_code == 201

    resp = client.post(
        f"/api/v1/appointments/{env['appointment_id']}/review",
        headers=headers,
        json={"rating": 5, "content": "很专业，收获很大"},
    )
    assert resp.status_code == 409

    reviews = client.get(f"/api/v1/coaches/{env['coach_id']}/reviews?page=1&pageSize=10").json()["data"]
    assert reviews["pagination"]["totalItems"] == 1
    assert reviews["items"][0]["rating"] == 5

    resp = client.get(f"/api/v1/appointments/{env['appointment_id']}/review", headers=headers)
    assert resp.status_code == 200

    mine = client.get("/api/v1/appointments/mine?page=1&pageSize=20", headers=headers).json()["data"]["items"]
    target = next(a for a in mine if a["id"] == env["appointment_id"])
    assert target["reviewed"] is True


def test_notifications(client, env):
    coach_count = client.get("/api/v1/notifications/unread-count", headers=env["coach_headers"]).json()["data"]["count"]
    assert coach_count >= 1

    user_count = client.get("/api/v1/notifications/unread-count", headers=env["user_headers"]).json()["data"]["count"]
    assert user_count >= 1

    resp = client.post("/api/v1/notifications/read-all", headers=env["user_headers"])
    assert resp.status_code == 200
    assert resp.json()["data"]["marked"] >= 1
    count = client.get("/api/v1/notifications/unread-count", headers=env["user_headers"]).json()["data"]["count"]
    assert count == 0


def test_checkin_and_badges(client, auth_headers):
    resp = client.post("/api/v1/check-ins", headers=auth_headers, json={"content": "睡前深呼吸五分钟"})
    assert resp.status_code == 201
    keys = [b["key"] for b in resp.json()["data"]["earnedBadges"]]
    assert "FIRST_CHECKIN" in keys

    resp = client.post("/api/v1/check-ins", headers=auth_headers, json={"content": "再来一次"})
    assert resp.status_code == 409

    stats = client.get("/api/v1/check-ins/stats", headers=auth_headers).json()["data"]
    assert stats["totalCount"] == 1
    assert stats["streakDays"] == 1

    badges = client.get("/api/v1/users/me/badges", headers=auth_headers).json()["data"]["items"]
    assert any(b["key"] == "FIRST_CHECKIN" for b in badges)

    month = date.today().strftime("%Y-%m")
    rows = client.get(f"/api/v1/check-ins?month={month}", headers=auth_headers).json()["data"]["items"]
    assert len(rows) == 1

    board = client.get("/api/v1/check-ins/leaderboard?period=month", headers=auth_headers)
    assert board.status_code == 200


def test_coach_clients_and_phrases(client, env):
    headers = env["coach_headers"]

    clients = client.get("/api/v1/coach/clients?page=1&pageSize=10", headers=headers).json()["data"]
    mine = next(c for c in clients["items"] if c["nickname"] == "评价用户")
    assert "****" in mine["phone"]
    relation_id = mine["id"]
    assert client.patch(
        f"/api/v1/coach/clients/{relation_id}", headers=headers, json={"remark": "注重亲子沟通"}
    ).json()["data"]["remark"] == "注重亲子沟通"

    library = client.get("/api/v1/phrase-library", headers=headers).json()["data"]["items"]
    assert len(library) >= 9

    saved = client.post("/api/v1/coach/phrases/save", headers=headers, json={"phraseId": 1})
    assert saved.status_code == 201
    assert saved.json()["data"]["source"] == "saved"
    assert client.post("/api/v1/coach/phrases/save", headers=headers, json={"phraseId": 1}).status_code == 409

    custom = client.post(
        "/api/v1/coach/phrases", headers=headers, json={"category": "ACTION", "content": "从最小的一步开始"}
    )
    assert custom.status_code == 201
    phrase_id = custom.json()["data"]["id"]
    assert client.patch(
        f"/api/v1/coach/phrases/{phrase_id}", headers=headers, json={"content": "从最小的一步开始，先做一分钟"}
    ).status_code == 200
    assert client.delete(f"/api/v1/coach/phrases/{phrase_id}", headers=headers).status_code == 204

    mine_phrases = client.get("/api/v1/coach/phrases", headers=headers).json()["data"]["items"]
    assert len(mine_phrases) >= 1


def test_leaderboard_group_by_user(client):
    nickname = "同名用户"
    phones = [unique_phone("134"), unique_phone("133")]
    try:
        last_headers = None
        for phone in phones:
            reg = client.post(
                "/api/v1/auth/register",
                json={"phone": phone, "password": "Test123456", "nickname": nickname, "privacyAgreed": True},
            )
            assert reg.status_code == 201
            headers = {"Authorization": f"Bearer {reg.json()['data']['accessToken']}"}
            last_headers = headers
            assert client.post("/api/v1/check-ins", headers=headers, json={"content": "打卡"}).status_code == 201

        board = client.get("/api/v1/check-ins/leaderboard?period=month", headers=last_headers).json()["data"]["items"]
        same = [item for item in board if item["nickname"] == nickname]
        assert len(same) == 2  # 同名用户按 user_id 分开计数
    finally:
        for phone in phones:
            delete_user_by_phone(phone)
