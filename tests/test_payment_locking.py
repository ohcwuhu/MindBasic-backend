"""支付锁定阶段一：余额 / 模拟支付 / 退款 / 超时释放。"""

import asyncio
import time as time_mod
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select, update

from app.core.exceptions import AppError
from app.db.session import AsyncSessionLocal, SessionLocal
from app.models.coach import Appointment, CoachSlot
from app.models.user import User
from app.models.v1_1 import Order, Payment, Refund
from app.services.appointment_service import cancel_my_appointment
from app.utils.time import utcnow_naive


def unique_phone(prefix: str) -> str:
    return prefix + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def delete_user_by_phone(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            order_ids = list(db.scalars(select(Order.id).where(Order.user_id == user.id)))
            if order_ids:
                db.execute(Payment.__table__.delete().where(Payment.order_id.in_(order_ids)))
                db.execute(Refund.__table__.delete().where(Refund.order_id.in_(order_ids)))
                db.execute(Order.__table__.delete().where(Order.id.in_(order_ids)))
            db.delete(user)
            db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def pay_env(client, admin_headers):
    coach_phone = unique_phone("137")
    user_phone = unique_phone("136")

    reg = client.post(
        "/api/v1/auth/register",
        json={"phone": coach_phone, "password": "Test123456", "nickname": "支付教练", "privacyAgreed": True, "serviceAgreed": True},
    )
    assert reg.status_code == 201
    coach_headers = {"Authorization": f"Bearer {reg.json()['data']['accessToken']}"}
    body = {
        "realName": "支付教练",
        "bio": "支付测试",
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
    mine = next(a for a in audits if a["coachName"] == "支付教练")
    assert client.post(f"/api/v1/admin/coach-audits/{mine['id']}/approve", headers=admin_headers).status_code == 200
    profile = client.get("/api/v1/coach/profile", headers=coach_headers).json()["data"]
    coach_id = profile["id"]
    service_id = profile["services"][0]["id"]

    db = SessionLocal()
    slot_ids = []
    try:
        day = date.today() + timedelta(days=2)
        s1 = CoachSlot(coach_id=coach_id, date=day, start_time=time(10, 0), end_time=time(11, 0), status="AVAILABLE")
        s2 = CoachSlot(coach_id=coach_id, date=day, start_time=time(14, 0), end_time=time(15, 0), status="AVAILABLE")
        s3 = CoachSlot(coach_id=coach_id, date=day, start_time=time(16, 0), end_time=time(17, 0), status="AVAILABLE")
        s4 = CoachSlot(coach_id=coach_id, date=day, start_time=time(18, 0), end_time=time(19, 0), status="AVAILABLE")
        db.add_all([s1, s2, s3, s4])
        db.commit()
        db.refresh(s1)
        db.refresh(s2)
        db.refresh(s3)
        db.refresh(s4)
        slot_ids = [s1.id, s2.id, s3.id, s4.id]
    finally:
        db.close()

    reg = client.post(
        "/api/v1/auth/register",
        json={"phone": user_phone, "password": "Test123456", "nickname": "支付用户", "privacyAgreed": True, "serviceAgreed": True},
    )
    assert reg.status_code == 201
    user_headers = {"Authorization": f"Bearer {reg.json()['data']['accessToken']}"}

    yield {
        "coach_headers": coach_headers,
        "user_headers": user_headers,
        "coach_id": coach_id,
        "service_id": service_id,
        "slot_ids": slot_ids,
    }

    db = SessionLocal()
    try:
        appt_ids = list(db.scalars(select(Appointment.id).where(Appointment.coach_id == coach_id)))
        if appt_ids:
            order_ids = list(db.scalars(select(Order.id).where(Order.appointment_id.in_(appt_ids))))
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


def book(client, headers, env, slot_index: int) -> dict:
    resp = client.post(
        "/api/v1/appointments",
        headers=headers,
        json={
            "coachId": env["coach_id"],
            "serviceId": env["service_id"],
            "slotId": env["slot_ids"][slot_index],
            "needDesc": "支付锁定测试",
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["paymentStatus"] == "CREATED"
    assert data["orderNo"] and data["amountInCents"] == 9900 and data["payExpireAt"]
    return data


def test_topup_and_wallet(client, pay_env):
    headers = pay_env["user_headers"]
    resp = client.post("/api/v1/wallet/topup", headers=headers, json={"amountInCents": 5000})
    assert resp.status_code == 201
    assert resp.json()["data"]["balanceInCents"] == 5000

    wallet = client.get("/api/v1/wallet", headers=headers).json()["data"]
    assert wallet["balanceInCents"] == 5000

    txs = client.get("/api/v1/wallet/transactions", headers=headers).json()["data"]["items"]
    assert any(t["bizType"] == "TOPUP" and t["changeInCents"] == 5000 for t in txs)


def test_pay_confirm_refund_flow(client, pay_env):
    headers = pay_env["user_headers"]
    data = book(client, headers, pay_env, 0)

    # 余额不足时不能支付
    resp = client.post(
        f"/api/v1/orders/{data['orderNo']}/pay", headers=headers, json={"method": "BALANCE"}
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "INSUFFICIENT_BALANCE"

    # 充值后余额支付
    before = client.get("/api/v1/wallet", headers=headers).json()["data"]["balanceInCents"]
    client.post("/api/v1/wallet/topup", headers=headers, json={"amountInCents": 9900})
    resp = client.post(
        f"/api/v1/orders/{data['orderNo']}/pay", headers=headers, json={"method": "BALANCE"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "PAID"
    wallet = client.get("/api/v1/wallet", headers=headers).json()["data"]
    assert wallet["balanceInCents"] == before  # 充值 9900 - 支付 9900

    mine = client.get("/api/v1/appointments/mine", headers=headers).json()["data"]["items"]
    item = next(a for a in mine if a["id"] == data["id"])
    assert item["paymentStatus"] == "PAID"

    changed = client.patch(
        f"/api/v1/coach/services/{pay_env['service_id']}",
        headers=pay_env["coach_headers"],
        json={"name": "改名后的服务", "priceInCents": 19900, "durationMin": 90},
    )
    assert changed.status_code == 200
    mine = client.get("/api/v1/appointments/mine", headers=headers).json()["data"]["items"]
    old_order = next(a for a in mine if a["id"] == data["id"])
    assert old_order["service"]["name"] == "单次咨询"
    assert old_order["service"]["durationMin"] == 60
    assert old_order["service"]["priceInCents"] == 9900
    assert old_order["amountInCents"] == 9900
    restored = client.patch(
        f"/api/v1/coach/services/{pay_env['service_id']}",
        headers=pay_env["coach_headers"],
        json={"name": "单次咨询", "priceInCents": 9900, "durationMin": 60},
    )
    assert restored.status_code == 200

    # 已支付后可确认
    resp = client.post(
        f"/api/v1/coach/appointments/{data['id']}/confirm", headers=pay_env["coach_headers"]
    )
    assert resp.status_code == 200

    # 免费窗口内取消 → 全额退款
    resp = client.post(f"/api/v1/appointments/{data['id']}/cancel", headers=headers, json={"cancelReason": "计划有变"})
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "CANCELLED"
    wallet = client.get("/api/v1/wallet", headers=headers).json()["data"]
    assert wallet["balanceInCents"] == before + 9900

    order = client.get(f"/api/v1/orders/{data['orderNo']}", headers=headers).json()["data"]
    assert order["status"] == "REFUNDED"
    txs = client.get("/api/v1/wallet/transactions", headers=headers).json()["data"]["items"]
    assert any(t["bizType"] == "REFUND" and t["changeInCents"] == 9900 for t in txs)


def test_expire_sweep_closes_unpaid(client, pay_env, admin_headers):
    headers = pay_env["user_headers"]
    data = book(client, headers, pay_env, 1)

    db = SessionLocal()
    try:
        db.execute(
            update(Order)
            .where(Order.appointment_id == data["id"])
            .values(expire_at=utcnow_naive() - timedelta(minutes=5))
        )
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/v1/orders/expire-sweep", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["closed"] >= 1

    mine = client.get("/api/v1/appointments/mine", headers=headers).json()["data"]["items"]
    item = next(a for a in mine if a["id"] == data["id"])
    assert item["status"] == "CANCELLED"
    assert "未支付" in (item["cancelReason"] or "")

    db = SessionLocal()
    try:
        slot = db.get(CoachSlot, pay_env["slot_ids"][1])
        assert slot.status == "AVAILABLE"
        order = db.scalar(select(Order).where(Order.appointment_id == data["id"]))
        assert order.status == "CLOSED"
    finally:
        db.close()


def test_admin_orders_list(client, pay_env, admin_headers):
    resp = client.get("/api/v1/admin/orders?page=1&pageSize=20", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["pagination"]["totalItems"] >= 1
    assert any(o["user"]["nickname"] == "支付用户" for o in resp.json()["data"]["items"])


def test_concurrent_cancel_refunds_only_once(client, pay_env):
    headers = pay_env["user_headers"]
    data = book(client, headers, pay_env, 2)
    client.post("/api/v1/wallet/topup", headers=headers, json={"amountInCents": 9900})
    paid = client.post(
        f"/api/v1/orders/{data['orderNo']}/pay",
        headers=headers,
        json={"method": "BALANCE"},
    )
    assert paid.status_code == 200

    async def _cancel_once() -> str:
        async with AsyncSessionLocal() as db:
            user = await db.scalar(select(User).where(User.nickname == "支付用户"))
            try:
                await cancel_my_appointment(db, user, data["id"], "并发取消测试")
                return "ok"
            except AppError as exc:
                await db.rollback()
                return exc.code

    async def _run_both():
        return await asyncio.gather(_cancel_once(), _cancel_once())

    results = asyncio.run(_run_both())
    assert results.count("ok") == 1
    assert results.count("INVALID_STATE_TRANSITION") == 1

    db = SessionLocal()
    try:
        order = db.scalar(select(Order).where(Order.order_no == data["orderNo"]))
        refunds = list(db.scalars(select(Refund).where(Refund.order_id == order.id)))
        assert order.status == "REFUNDED"
        assert len(refunds) == 1
        historical_appointment = db.get(Appointment, data["id"])
        assert historical_appointment is not None
        historical_slot_id = int(historical_appointment.slot_id)
    finally:
        db.close()

    day = (date.today() + timedelta(days=2)).isoformat()
    replaced = client.put(
        "/api/v1/coach/slots",
        headers=pay_env["coach_headers"],
        json={"slots": [{"date": day, "startTime": "18:00", "endTime": "19:00"}]},
    )
    assert replaced.status_code == 200

    db = SessionLocal()
    try:
        historical_slot = db.get(CoachSlot, historical_slot_id)
        assert historical_slot is not None
        assert historical_slot.status == "OFF"
        assert db.get(Appointment, data["id"]) is not None
    finally:
        db.close()


def test_effective_no_show_filter_matches_item_state(client, pay_env):
    headers = pay_env["user_headers"]
    data = book(client, headers, pay_env, 3)
    client.post("/api/v1/wallet/topup", headers=headers, json={"amountInCents": 9900})
    paid = client.post(
        f"/api/v1/orders/{data['orderNo']}/pay",
        headers=headers,
        json={"method": "BALANCE"},
    )
    assert paid.status_code == 200

    db = SessionLocal()
    try:
        slot = db.get(CoachSlot, pay_env["slot_ids"][3])
        slot.date = date.today() - timedelta(days=1)
        slot.start_time = time(10, 0)
        slot.end_time = time(11, 0)
        db.commit()
    finally:
        db.close()

    no_show = client.get("/api/v1/appointments/mine?status=NO_SHOW", headers=headers).json()["data"]
    assert any(item["id"] == data["id"] and item["status"] == "NO_SHOW" for item in no_show["items"])
    assert no_show["pagination"]["totalItems"] == len(no_show["items"])

    pending = client.get("/api/v1/appointments/mine?status=PENDING", headers=headers).json()["data"]
    assert all(item["id"] != data["id"] for item in pending["items"])
