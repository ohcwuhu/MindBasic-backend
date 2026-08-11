"""成长测评：量表、提交评分、报告推荐与历史。"""


def _answers_for(all_high: bool = True) -> dict[str, int]:
    return {str(qid): (5 if all_high else 2) for qid in range(1, 16)}


def test_template_requires_login(client):
    resp = client.get("/api/v1/growth-assessments/template")
    assert resp.status_code == 401


def test_template_and_submit(client, auth_headers):
    resp = client.get("/api/v1/growth-assessments/template", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "成长状态自评"
    assert len(data["questions"]) == 15
    assert len({q["dimensionKey"] for q in data["questions"]}) == 5
    assert data["questions"][0]["options"][0] == {"value": 1, "label": "几乎从不"}

    # 全高分：五维都是优势，无推荐
    resp = client.post(
        "/api/v1/growth-assessments",
        headers=auth_headers,
        json={"answers": _answers_for(all_high=True)},
    )
    assert resp.status_code == 201
    result = resp.json()["data"]
    assert len(result["scores"]) == 5
    assert all(s["level"] == "HAS_STRENGTH" for s in result["scores"])
    assert "优势" in result["report"]["summary"]
    assert result["report"]["recommendations"]["selfCoaching"] == []
    assert result["report"]["recommendations"]["coachTags"] == []

    # 全低分：给出模板与教练方向推荐
    resp = client.post(
        "/api/v1/growth-assessments",
        headers=auth_headers,
        json={"answers": _answers_for(all_high=False)},
    )
    assert resp.status_code == 201
    low = resp.json()["data"]
    assert all(s["level"] == "GROWTH_SPACE" for s in low["scores"])
    assert low["report"]["recommendations"]["selfCoaching"]
    assert low["report"]["recommendations"]["coachTags"]

    resp = client.get("/api/v1/growth-assessments?page=1&pageSize=10", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["pagination"]["totalItems"] == 2

    resp = client.get(f"/api/v1/growth-assessments/{result['id']}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == result["id"]


def test_submit_invalid_answers(client, auth_headers):
    resp = client.post(
        "/api/v1/growth-assessments",
        headers=auth_headers,
        json={"answers": {str(qid): 6 for qid in range(1, 16)}},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"

    resp = client.post(
        "/api/v1/growth-assessments",
        headers=auth_headers,
        json={"answers": {"1": 3}},
    )
    assert resp.status_code == 400
