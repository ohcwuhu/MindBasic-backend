def test_create_and_list_journals(client, auth_headers):
    resp = client.post(
        "/api/v1/emotion-journals",
        headers=auth_headers,
        json={"moodType": "ANXIOUS", "content": "明天要汇报，心里很乱"},
    )
    assert resp.status_code == 201
    body = resp.json()["data"]
    assert body["moodType"] == "ANXIOUS"
    assert body["feedback"] and body["feedback"] != body["content"]
    first_id = body["id"]

    resp = client.post(
        "/api/v1/emotion-journals",
        headers=auth_headers,
        json={"moodType": "CALM", "content": "今天完成了一件拖了很久的事"},
    )
    assert resp.status_code == 201
    second_id = resp.json()["data"]["id"]

    resp = client.get("/api/v1/emotion-journals?page=1&pageSize=1", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["items"]) == 1
    assert data["pagination"]["totalItems"] == 2
    assert data["pagination"]["hasMore"] is True
    assert data["items"][0]["id"] == second_id  # 时间倒序

    resp = client.delete(f"/api/v1/emotion-journals/{first_id}", headers=auth_headers)
    assert resp.status_code == 204
    resp = client.delete(f"/api/v1/emotion-journals/{first_id}", headers=auth_headers)
    assert resp.status_code == 404


def test_invalid_mood_type(client, auth_headers):
    resp = client.post(
        "/api/v1/emotion-journals",
        headers=auth_headers,
        json={"moodType": "UNKNOWN", "content": "测试"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_journal_trend(client, auth_headers):
    for mood in ("CALM", "CALM", "ANXIOUS"):
        resp = client.post(
            "/api/v1/emotion-journals",
            headers=auth_headers,
            json={"moodType": mood, "content": f"趋势测试-{mood}"},
        )
        assert resp.status_code == 201

    resp = client.get("/api/v1/emotion-journals/trend?days=30", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["days"] == 30
    assert len(data["items"]) == 30
    # 模块内同用户可能残留其他测试日记，用 >= 断言避免耦合
    assert data["summary"]["CALM"] >= 2
    assert data["summary"]["ANXIOUS"] >= 1
    assert data["items"][-1]["moods"].get("CALM", 0) >= 2

    resp = client.get("/api/v1/emotion-journals/trend?days=7", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["days"] == 7

    resp = client.get("/api/v1/emotion-journals/trend?days=3", headers=auth_headers)
    assert resp.status_code == 400


def test_journal_calendar(client, auth_headers):
    for mood in ("CALM", "ANXIOUS"):
        resp = client.post(
            "/api/v1/emotion-journals",
            headers=auth_headers,
            json={"moodType": mood, "content": f"月历测试-{mood}"},
        )
        assert resp.status_code == 201

    resp = client.get("/api/v1/emotion-journals/calendar", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert 28 <= len(data["days"]) <= 31
    assert sum(day["count"] for day in data["days"]) >= 2
    assert data["summary"]["CALM"] >= 1
    assert data["summary"]["ANXIOUS"] >= 1

    resp = client.get("/api/v1/emotion-journals/calendar?month=2026-13", headers=auth_headers)
    assert resp.status_code == 400
