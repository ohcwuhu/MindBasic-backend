"""AI 自我教练对话持久化：会话与消息入库 + 记录接口。"""

import asyncio
import time as time_mod

from sqlalchemy import select

from app.db.session import AsyncSessionLocal, SessionLocal
from app.models.user import User
from app.services.ai_conversation_service import (
    append_ai_message,
    end_ai_conversation,
    get_or_create_active_conversation,
)


def unique_phone() -> str:
    return "129" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def register(client) -> dict:
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Test123456",
            "nickname": "AI记录测试",
            "privacyAgreed": True,
            "serviceAgreed": True,
        },
    )
    assert resp.status_code == 201
    return {
        "phone": phone,
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


async def _seed_conversation(user_id: int) -> int:
    async with AsyncSessionLocal() as db:
        conv = await get_or_create_active_conversation(db, user_id)
        await append_ai_message(
            db,
            conv.id,
            "USER",
            "最近学习压力特别大",
            emotion={"fusion_emotion_cn": "焦虑", "fusion_confidence": 0.8},
        )
        await append_ai_message(db, conv.id, "ASSISTANT", "听起来最近确实很辛苦，压力主要来自哪里呢？")
        await end_ai_conversation(db, conv.id)
        return conv.id


def test_ai_conversation_persistence(client, monkeypatch):
    # 测试不调用真实 LLM 总结：禁用 DeepSeek，走启发式摘要
    monkeypatch.setattr("app.services.ai_conversation_service.settings.deepseek_api_key", "")
    acc = register(client)
    other = register(client)
    try:
        db = SessionLocal()
        try:
            user_id = db.scalar(select(User.id).where(User.phone == acc["phone"]))
            other_id = db.scalar(select(User.id).where(User.phone == other["phone"]))
        finally:
            db.close()

        conv_id = asyncio.run(_seed_conversation(user_id))

        # 列表
        listed = client.get("/api/v1/ai-conversations?page=1&pageSize=10", headers=acc["headers"])
        assert listed.status_code == 200
        items = listed.json()["data"]["items"]
        mine = next(i for i in items if i["id"] == conv_id)
        assert mine["title"] == "最近学习压力特别大"
        assert mine["status"] == "ENDED"
        assert mine["messageCount"] == 2

        # 详情
        detail = client.get(f"/api/v1/ai-conversations/{conv_id}", headers=acc["headers"])
        assert detail.status_code == 200
        data = detail.json()["data"]
        roles = [m["role"] for m in data["messages"]]
        assert roles == ["USER", "ASSISTANT"]
        assert data["messages"][0]["emotion"]["fusion_emotion_cn"] == "焦虑"

        # 非本人不可见
        other_view = client.get(f"/api/v1/ai-conversations/{conv_id}", headers=other["headers"])
        assert other_view.status_code == 404

        # 总结 → 返回草稿（不落库）
        resp = client.post(f"/api/v1/ai-conversations/{conv_id}/summary", headers=acc["headers"])
        assert resp.status_code == 201
        draft = resp.json()["data"]
        assert draft["moodType"] == "ANXIOUS"
        assert draft["source"] == "SELF_COACHING"
        assert draft["conversationId"] == conv_id
        assert "学习压力" in draft["content"]

        # 提交为情绪日记（标注自我教练来源）
        resp = client.post(
            "/api/v1/emotion-journals",
            headers=acc["headers"],
            json={
                "moodType": draft["moodType"],
                "content": draft["content"],
                "source": "SELF_COACHING",
                "sourceConversationId": conv_id,
            },
        )
        assert resp.status_code == 201
        journal = resp.json()["data"]
        assert journal["source"] == "SELF_COACHING"
        assert journal["sourceConversationId"] == conv_id
        assert journal["feedback"]

        # 幂等：同一对话只能生成一次
        again = client.post(
            "/api/v1/emotion-journals",
            headers=acc["headers"],
            json={
                "moodType": "CALM",
                "content": "重复",
                "source": "SELF_COACHING",
                "sourceConversationId": conv_id,
            },
        )
        assert again.status_code == 409

        # 会话关联上 journalId，且情绪日记列表可见
        detail = client.get(f"/api/v1/ai-conversations/{conv_id}", headers=acc["headers"]).json()["data"]
        assert detail["conversation"]["journalId"] == journal["id"]
        journals = client.get("/api/v1/emotion-journals?page=1&pageSize=10", headers=acc["headers"]).json()["data"]["items"]
        assert any(j["id"] == journal["id"] for j in journals)
    finally:
        cleanup(acc["phone"])
        cleanup(other["phone"])
