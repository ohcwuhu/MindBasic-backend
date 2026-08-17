"""通用限流 + AI 端点鉴权。"""

import time as time_mod


def unique_phone() -> str:
    return "130" + str(int(time_mod.time() * 1000) % 100000000).zfill(8)


def register(client) -> dict:
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "phone": unique_phone(),
            "password": "Test123456",
            "nickname": "限流测试",
            "privacyAgreed": True,
            "serviceAgreed": True,
        },
    )
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}


def test_data_export_rate_limit(client):
    headers = register(client)
    codes = []
    for _ in range(4):
        resp = client.post("/api/v1/users/me/data-export", headers=headers)
        codes.append(resp.status_code)
    assert codes[:3] == [201, 201, 201]
    assert codes[3] == 429


def test_ai_endpoints_require_auth(client):
    # 未登录访问 AI 接口 → 401
    resp = client.get("/api/analyze_audio/config_check")
    assert resp.status_code == 401

    resp = client.post("/api/ai_coach/chat", json={"messages": []})
    assert resp.status_code == 401

    # 普通用户访问仅管理员接口 → 403
    user_headers = register(client)
    resp = client.get("/api/analyze_audio/config_check", headers=user_headers)
    assert resp.status_code == 403

    resp = client.post("/api/analyze_audio/warmup", headers=user_headers)
    assert resp.status_code == 403
