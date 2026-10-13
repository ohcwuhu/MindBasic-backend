"""数据合规：导出 / 注销删除 / 审计日志。"""

import time as time_mod

from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.services.data_export_service import EXPORT_DIR
from app.models.user import User


def unique_phone() -> str:
    return "131" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def register(client) -> dict:
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Test123456",
            "nickname": "合规用户",
            "privacyAgreed": True,
            "serviceAgreed": True,
        },
    )
    assert resp.status_code == 201
    return {
        "phone": phone,
        "token": resp.json()["data"]["accessToken"],
        "headers": {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"},
    }


def cleanup(phone: str) -> None:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


def hard_delete(user_id: int) -> None:
    """注销走的是「匿名化用户行」，所以测试收尾要按 id 删，不能按手机号找。"""
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        db.commit()
    finally:
        db.close()


def _user_id(phone: str) -> int:
    db = SessionLocal()
    try:
        return int(db.scalar(select(User.id).where(User.phone == phone)))
    finally:
        db.close()


def _count(table: str, user_id: int | None) -> int:
    """按 user_id 数行；user_id 传 None 表示数「已经解绑」的那些。"""
    db = SessionLocal()
    try:
        if user_id is None:
            sql = text(f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL")
            return int(db.scalar(sql) or 0)
        sql = text(f"SELECT COUNT(*) FROM {table} WHERE user_id = :uid")
        return int(db.scalar(sql, {"uid": user_id}) or 0)
    finally:
        db.close()


def _seed_runtime_rows(user_id: int) -> None:
    """真实使用时由实时管线写进去的两类行：分析留痕与采集授权。"""
    db = SessionLocal()
    try:
        db.execute(
            text(
                "INSERT INTO multimodal_analysis_records (user_id, source, status, asr_text) "
                "VALUES (:uid, 'VIDEO_CALL', 'ok', '注销前的转写')"
            ),
            {"uid": user_id},
        )
        db.execute(
            text(
                "INSERT INTO consent_records (user_id, policy_version, scopes, source, ip, user_agent) "
                "VALUES (:uid, 'v1.2', :scopes, 'VIDEO_CALL', '127.0.0.1', 'selftest')"
            ),
            {"uid": user_id, "scopes": '{"mic": true, "camera": false}'},
        )
        db.commit()
    finally:
        db.close()


def test_data_export_flow(client, admin_headers):
    acc = register(client)
    try:
        created = client.post("/api/v1/users/me/data-export", headers=acc["headers"])
        assert created.status_code == 201
        export_id = created.json()["data"]["id"]
        assert created.json()["data"]["status"] == "READY"

        listed = client.get("/api/v1/users/me/data-exports", headers=acc["headers"]).json()["data"]
        assert listed["pagination"]["totalItems"] >= 1
        assert any(i["id"] == export_id for i in listed["items"])

        down = client.get(f"/api/v1/users/me/data-exports/{export_id}/download", headers=acc["headers"])
        assert down.status_code == 200
        body = down.json()
        assert "profile" in body and "conversations" in body and "wallet_transactions" in body
        assert body["profile"]["nickname"] == "合规用户"

        logs = client.get("/api/v1/admin/audit-logs?action=USER_DATA_EXPORT&page=1&pageSize=10", headers=admin_headers)
        assert logs.status_code == 200
        assert any(i["action"] == "USER_DATA_EXPORT" for i in logs.json()["data"]["items"])
    finally:
        cleanup(acc["phone"])


def test_delete_account_requires_password_and_purges(client, admin_headers):
    acc = register(client)
    account_id = _user_id(acc["phone"])
    try:
        # 先在这个账号名下放几样东西：一条日记、一次打卡、一次测评、一个导出包
        journal = client.post(
            "/api/v1/emotion-journals",
            headers=acc["headers"],
            json={"moodType": "CALM", "content": "注销前写的一条日记"},
        )
        assert journal.status_code == 201
        assert client.post("/api/v1/check-ins", headers=acc["headers"], json={"content": "注销前打的一次卡"}).status_code == 201
        template = client.get("/api/v1/growth-assessments/template", headers=acc["headers"]).json()["data"]
        answers = {str(q["id"]): 4 for q in template["questions"]}
        assert client.post("/api/v1/growth-assessments", headers=acc["headers"], json={"answers": answers}).status_code == 201
        exported = client.post("/api/v1/users/me/data-export", headers=acc["headers"])
        assert exported.status_code == 201
        export_file = exported.json()["data"].get("fileId") or exported.json()["data"].get("file_id")

        # 只在真实使用中才会产生的那几类行（分析留痕、采集授权）直接落库，省得为测试打通整条实时管线
        _seed_runtime_rows(account_id)

        resp = client.post(
            "/api/v1/users/me/delete",
            headers=acc["headers"],
            json={"password": "WrongPass123"},
        )
        assert resp.status_code == 401

        resp = client.post(
            "/api/v1/users/me/delete",
            headers=acc["headers"],
            json={"password": "Test123456"},
        )
        assert resp.status_code == 200

        # 删除后原 token 失效
        me = client.get("/api/v1/users/me", headers=acc["headers"])
        assert me.status_code == 401

        # 自助内容真的没了（这几张表以前漏了，注销后还留着用户的分数与对话）
        assert _count("emotion_journals", account_id) == 0
        assert _count("check_ins", account_id) == 0
        assert _count("growth_assessment_results", account_id) == 0
        assert _count("ai_conversations", account_id) == 0
        assert _count("data_exports", account_id) == 0
        if export_file:
            assert not (EXPORT_DIR / export_file).is_file()

        # 统计用的留痕保留，但和这个人不再有关联
        assert _count("multimodal_analysis_records", account_id) == 0
        assert _count("multimodal_analysis_records", None) >= 1
        assert _count("consent_records", account_id) == 0
        assert _count("consent_records", None) >= 1

        logs = client.get("/api/v1/admin/audit-logs?action=USER_DELETE&page=1&pageSize=10", headers=admin_headers)
        assert logs.status_code == 200
        assert any(i["action"] == "USER_DELETE" for i in logs.json()["data"]["items"])
    finally:
        hard_delete(account_id)
