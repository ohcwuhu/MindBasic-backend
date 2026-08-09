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
