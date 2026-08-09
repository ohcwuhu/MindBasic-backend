import time as time_mod
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.coach import Appointment, CoachProfile, CoachSlot
from app.models.user import User


def unique_phone() -> str:
    return "138" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


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
    """搭建已审核通过的教练：注册→提交资料→管理员通过→生成时段。"""
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "教练测试", "privacyAgreed": True},
    )
    assert resp.status_code == 201
    coach_headers = {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}

    body = {
        "realName": "教练测试",
        "bio": "专注考前陪伴",
        "trainingExp": "心理教练认证培训",
        "serviceConcept": "赋能、陪伴",
        "yearsOfExperience": 3,
        "credentialUrls": [],
        "idCardUrl": None,
        "tagIds": [1],
        "services": [
            {"name": "单次咨询", "serviceType": "SINGLE", "durationMin": 60, "priceInCents": 9900},
        ],
    }
    resp = client.post("/api/v1/coach/profile", headers=coach_headers, json=body)
    assert resp.status_code == 201

    resp = client.get(
        "/api/v1/admin/coach-audits?status=PENDING&page=1&pageSize=10",
        headers=admin_headers,
    )
    audits = resp.json()["data"]["items"]
    mine = next(a for a in audits if a["coachName"] == "教练测试")
    resp = client.post(f"/api/v1/admin/coach-audits/{mine['id']}/approve", headers=admin_headers)
    assert resp.status_code == 200

    resp = client.get("/api/v1/coach/profile", headers=coach_headers)
    profile = resp.json()["data"]
    coach_id = profile["id"]
    service_id = profile["services"][0]["id"]

    db = SessionLocal()
    slots = []
    try:
        day = date.today() + timedelta(days=1)
        s1 = CoachSlot(coach_id=coach_id, date=day, start_time=time(10, 0), end_time=time(11, 0), status="AVAILABLE")
        s2 = CoachSlot(coach_id=coach_id, date=day, start_time=time(14, 0), end_time=time(15, 0), status="AVAILABLE")
        db.add_all([s1, s2])
        db.commit()
        db.refresh(s1)
        db.refresh(s2)
        slots = [s1.id, s2.id]
    finally:
        db.close()

    yield {
        "coach_id": coach_id,
        "service_id": service_id,
        "slot_ids": slots,
        "coach_headers": coach_headers,
    }

    db = SessionLocal()
    try:
        db.execute(Appointment.__table__.delete().where(Appointment.coach_id == coach_id))
        db.commit()
    finally:
        db.close()
    delete_user_by_phone(phone)


def book(client, auth_headers, coach_env, slot_index: int) -> int:
    resp = client.post(
        "/api/v1/appointments",
        headers=auth_headers,
        json={
            "coachId": coach_env["coach_id"],
            "serviceId": coach_env["service_id"],
            "slotId": coach_env["slot_ids"][slot_index],
            "needDesc": "希望学会考前如何稳定心态",
        },
    )
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


def test_booking_confirm_complete(client, auth_headers, coach_env):
    appointment_id = book(client, auth_headers, coach_env, 0)

    resp = client.get("/api/v1/appointments/mine", headers=auth_headers)
    mine = next(a for a in resp.json()["data"]["items"] if a["id"] == appointment_id)
    assert mine["status"] == "PENDING" and mine["canCancel"] is True

    resp = client.get(
        "/api/v1/coach/appointments?status=PENDING&page=1&pageSize=10",
        headers=coach_env["coach_headers"],
    )
    coach_item = next(a for a in resp.json()["data"]["items"] if a["id"] == appointment_id)
    assert "****" in coach_item["user"]["phone"]

    resp = client.post(
        f"/api/v1/coach/appointments/{appointment_id}/confirm",
        headers=coach_env["coach_headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "CONFIRMED"

    resp = client.post(
        f"/api/v1/coach/appointments/{appointment_id}/confirm",
        headers=coach_env["coach_headers"],
    )
    assert resp.status_code == 409

    resp = client.post(
        f"/api/v1/coach/appointments/{appointment_id}/complete",
        headers=coach_env["coach_headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "COMPLETED"


def test_case_records_flow(client, coach_env):
    # 手动个案（无预约关联）
    resp = client.post(
        "/api/v1/coach/cases",
        headers=coach_env["coach_headers"],
        json={
            "clientNickname": "小满",
            "keyPoints": "考前焦虑，资源盘点后找到稳定节奏的方法",
            "userGains": "学会了深呼吸与正面自我对话",
            "followupAdvice": "考前一周每天练习 5 分钟",
            "durationMin": 60,
        },
    )
    assert resp.status_code == 201
    manual_id = resp.json()["data"]["id"]

    # 为已完成预约建个案
    resp = client.get(
        "/api/v1/coach/appointments?status=COMPLETED&page=1&pageSize=10",
        headers=coach_env["coach_headers"],
    )
    completed = resp.json()["data"]["items"][0]
    resp = client.post(
        "/api/v1/coach/cases",
        headers=coach_env["coach_headers"],
        json={"appointmentId": completed["id"], "clientNickname": "小圆", "durationMin": 60},
    )
    assert resp.status_code == 201
    linked_id = resp.json()["data"]["id"]

    resp = client.post(
        "/api/v1/coach/cases",
        headers=coach_env["coach_headers"],
        json={"appointmentId": completed["id"], "durationMin": 30},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"

    resp = client.get("/api/v1/coach/cases?page=1&pageSize=10", headers=coach_env["coach_headers"])
    assert resp.json()["data"]["pagination"]["totalItems"] == 2

    resp = client.patch(
        f"/api/v1/coach/cases/{linked_id}",
        headers=coach_env["coach_headers"],
        json={"followupAdvice": "每周复盘一次"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["followupAdvice"] == "每周复盘一次"

    resp = client.get("/api/v1/coach/cases/stats", headers=coach_env["coach_headers"])
    stats = resp.json()["data"]
    assert stats["totalCases"] == 2
    assert stats["serviceMinutes"] == 120
    assert stats["clientCount"] == 2

    resp = client.delete(f"/api/v1/coach/cases/{manual_id}", headers=coach_env["coach_headers"])
    assert resp.status_code == 204


def test_cancel_releases_slot_and_blocked_transitions(client, auth_headers, coach_env):
    appointment_id = book(client, auth_headers, coach_env, 1)
    resp = client.post(
        f"/api/v1/coach/appointments/{appointment_id}/cancel",
        headers=coach_env["coach_headers"],
        json={"cancelReason": "用户临时有事，双方协商取消"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "CANCELLED"

    db = SessionLocal()
    try:
        slot = db.get(CoachSlot, coach_env["slot_ids"][1])
        assert slot.status == "AVAILABLE"
    finally:
        db.close()

    resp = client.post(
        f"/api/v1/coach/appointments/{appointment_id}/complete",
        headers=coach_env["coach_headers"],
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


def test_unauthorized_coach_access(client):
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "路人", "privacyAgreed": True},
    )
    headers = {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}
    try:
        resp = client.get("/api/v1/coach/appointments", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "COACH_NOT_APPROVED"
    finally:
        delete_user_by_phone(phone)
