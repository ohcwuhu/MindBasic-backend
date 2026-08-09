EXPECTED_STEP_KEYS = ["STATUS", "IDEAL", "RESOURCES", "ACTION"]


def test_templates_list_and_detail(client, auth_headers):
    resp = client.get("/api/v1/self-coaching/templates", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 5
    first = items[0]
    assert [s["stepKey"] for s in first["steps"]] == EXPECTED_STEP_KEYS

    resp = client.get("/api/v1/self-coaching/templates/1", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "考前焦虑调节"

    resp = client.get("/api/v1/self-coaching/templates/99999", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["code"] == "TEMPLATE_NOT_FOUND"


def test_create_draft_and_complete(client, auth_headers):
    resp = client.post(
        "/api/v1/self-coaching/records",
        headers=auth_headers,
        json={
            "templateId": 1,
            "answers": {"STATUS": "最近有点紧张，担心考不好"},
            "status": "DRAFT",
        },
    )
    assert resp.status_code == 201
    body = resp.json()["data"]
    record_id = body["id"]
    assert body["status"] == "DRAFT"
    assert body["actionCard"] is None

    # 未答完就完成 → 400
    resp = client.patch(
        f"/api/v1/self-coaching/records/{record_id}",
        headers=auth_headers,
        json={"answers": {"IDEAL": "状态平稳"}, "status": "COMPLETED"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"

    # 补全后完成 → 生成行动卡
    resp = client.patch(
        f"/api/v1/self-coaching/records/{record_id}",
        headers=auth_headers,
        json={
            "answers": {
                "IDEAL": "状态平稳地走进考场",
                "RESOURCES": "过去模拟考都顺利完成了",
                "ACTION": "每天睡前做一次深呼吸练习",
            },
            "status": "COMPLETED",
        },
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "COMPLETED"
    assert body["actionCard"]["title"] == "考前焦虑调节 · 成长行动卡"
    assert "深呼吸" in body["actionCard"]["content"]

    # 完成后不允许退回草稿
    resp = client.patch(
        f"/api/v1/self-coaching/records/{record_id}",
        headers=auth_headers,
        json={"status": "DRAFT"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


def test_record_ownership_and_list(client, auth_headers):
    resp = client.get("/api/v1/self-coaching/records/999999", headers=auth_headers)
    assert resp.status_code == 404

    resp = client.get("/api/v1/self-coaching/records?page=1&pageSize=10", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["pagination"]["totalItems"] >= 1
    assert data["pagination"]["hasMore"] is False
    assert data["items"][0]["templateName"] == "考前焦虑调节"
