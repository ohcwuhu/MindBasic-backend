"""平台系统配置（管理端）与公开合规信息（热线/免责声明）。"""


def test_public_config_contains_compliance_info(client):
    resp = client.get("/api/v1/platform/config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["platformName"]
    assert data["hotline"] == "12356"
    assert "12356" in data["emergencyHint"]
    assert "免责声明" in data["disclaimer"] or "不提供" in data["disclaimer"]


def test_admin_get_configs(client, admin_headers):
    resp = client.get("/api/v1/admin/system-configs", headers=admin_headers)
    assert resp.status_code == 200
    keys = {item["key"] for item in resp.json()["data"]["items"]}
    assert keys == {
        "platform_name",
        "hotline",
        "emergency_hint",
        "disclaimer",
        "agreement_version",
        "agreement_content",
    }


def test_admin_update_config_reflected_publicly(client, admin_headers):
    resp = client.put(
        "/api/v1/admin/system-configs",
        headers=admin_headers,
        json={"items": [{"key": "platform_name", "value": "MindBasic 测试"}]},
    )
    assert resp.status_code == 200
    try:
        public = client.get("/api/v1/platform/config").json()["data"]
        assert public["platformName"] == "MindBasic 测试"
    finally:
        client.put(
            "/api/v1/admin/system-configs",
            headers=admin_headers,
            json={"items": [{"key": "platform_name", "value": "MindBasic"}]},
        )


def test_admin_update_invalid_key_rejected(client, admin_headers):
    resp = client.put(
        "/api/v1/admin/system-configs",
        headers=admin_headers,
        json={"items": [{"key": "not_a_real_key", "value": "x"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_admin_update_empty_value_rejected(client, admin_headers):
    resp = client.put(
        "/api/v1/admin/system-configs",
        headers=admin_headers,
        json={"items": [{"key": "hotline", "value": "   "}]},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_admin_config_requires_admin(client, auth_headers):
    resp = client.get("/api/v1/admin/system-configs", headers=auth_headers)
    assert resp.status_code == 403
    resp = client.put(
        "/api/v1/admin/system-configs",
        headers=auth_headers,
        json={"items": [{"key": "hotline", "value": "12356"}]},
    )
    assert resp.status_code == 403
