import time

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.user import User


def second_coach_headers(client):
    phone = "138" + str(int(time.time() * 1000) % 100000000).zfill(8)
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "驳回测试", "privacyAgreed": True},
    )
    assert resp.status_code == 201
    token = resp.json()["data"]["accessToken"]
    headers = {"Authorization": f"Bearer {token}"}
    return headers, phone


def delete_user_by_phone(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


PROFILE_BODY = {
    "realName": "测试教练",
    "bio": "专注考前陪伴与亲子沟通",
    "trainingExp": "心理教练认证培训 120 学时",
    "serviceConcept": "赋能、陪伴、资源导向",
    "yearsOfExperience": 3,
    "credentialUrls": ["http://example.com/cert.pdf"],
    "idCardUrl": "http://example.com/idcard.jpg",
    "tagIds": [1, 2],
    "services": [
        {"name": "单次咨询", "serviceType": "SINGLE", "durationMin": 60, "priceInCents": 9900},
        {"name": "高考5次陪伴卡", "serviceType": "PACKAGE", "durationMin": 60, "priceInCents": 99000},
    ],
}


def test_submit_and_view_profile(client, auth_headers):
    resp = client.post("/api/v1/coach/profile", headers=auth_headers, json=PROFILE_BODY)
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["auditStatus"] == "PENDING"
    assert len(data["tags"]) == 2
    assert len(data["services"]) == 2

    resp = client.post("/api/v1/coach/profile", headers=auth_headers, json=PROFILE_BODY)
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"

    resp = client.get("/api/v1/coach/profile", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == data["id"]


def test_admin_approve_flow(client, auth_headers, admin_headers):
    resp = client.get(
        "/api/v1/admin/coach-audits?status=PENDING&page=1&pageSize=10",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    audits = resp.json()["data"]["items"]
    mine = next(a for a in audits if a["coachName"] == "接口测试")
    audit_id = mine["id"]
    assert mine["status"] == "PENDING"

    resp = client.get(f"/api/v1/admin/coach-audits/{audit_id}", headers=admin_headers)
    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["snapshot"]["realName"] == "测试教练"
    assert "****" in detail["phone"]

    resp = client.post(f"/api/v1/admin/coach-audits/{audit_id}/approve", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "APPROVED"

    resp = client.post(f"/api/v1/admin/coach-audits/{audit_id}/approve", headers=admin_headers)
    assert resp.status_code == 409
    assert resp.json()["code"] == "AUDIT_ALREADY_PROCESSED"

    resp = client.get("/api/v1/coach/profile", headers=auth_headers)
    assert resp.json()["data"]["auditStatus"] == "APPROVED"


def test_patch_after_approved_triggers_reauth(client, auth_headers, admin_headers):
    resp = client.patch(
        "/api/v1/coach/profile",
        headers=auth_headers,
        json={"bio": "更新后的简介：新增家庭教育方向"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["auditStatus"] == "PENDING"

    resp = client.get(
        "/api/v1/admin/coach-audits?status=PENDING&page=1&pageSize=10",
        headers=admin_headers,
    )
    audits = resp.json()["data"]["items"]
    mine = next(a for a in audits if a["coachName"] == "接口测试")
    assert mine["submitVersion"] == 2

    resp = client.post(f"/api/v1/admin/coach-audits/{mine['id']}/approve", headers=admin_headers)
    assert resp.status_code == 200


def test_reject_and_resubmit(client, admin_headers):
    headers, phone = second_coach_headers(client)
    try:
        resp = client.post("/api/v1/coach/profile", headers=headers, json=PROFILE_BODY)
        assert resp.status_code == 201

        resp = client.get(
            "/api/v1/admin/coach-audits?status=PENDING&page=1&pageSize=10",
            headers=admin_headers,
        )
        audits = resp.json()["data"]["items"]
        mine = next(a for a in audits if a["coachName"] == "驳回测试")

        resp = client.post(
            f"/api/v1/admin/coach-audits/{mine['id']}/reject",
            headers=admin_headers,
            json={"reason": "培训经历不完整，请补充学时与督导记录"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "REJECTED"

        resp = client.get("/api/v1/coach/profile", headers=headers)
        data = resp.json()["data"]
        assert data["auditStatus"] == "REJECTED"
        assert "督导记录" in data["auditRemark"]

        # 重新提交
        resp = client.post("/api/v1/coach/profile/submit-audit", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["auditStatus"] == "PENDING"

        # 审核中重复提交 → 409
        resp = client.post("/api/v1/coach/profile/submit-audit", headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "INVALID_STATE_TRANSITION"

        resp = client.get(
            "/api/v1/admin/coach-audits?status=PENDING&page=1&pageSize=10",
            headers=admin_headers,
        )
        mine = next(a for a in resp.json()["data"]["items"] if a["coachName"] == "驳回测试")
        assert mine["submitVersion"] == 2
        resp = client.post(f"/api/v1/admin/coach-audits/{mine['id']}/approve", headers=admin_headers)
        assert resp.status_code == 200
    finally:
        delete_user_by_phone(phone)


def test_submit_with_invalid_tag(client, auth_headers):
    headers, phone = second_coach_headers(client)
    body = dict(PROFILE_BODY)
    body["tagIds"] = [99999]
    try:
        resp = client.post("/api/v1/coach/profile", headers=headers, json=body)
        assert resp.status_code == 400
        assert resp.json()["code"] == "VALIDATION_ERROR"
    finally:
        delete_user_by_phone(phone)
