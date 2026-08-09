import base64

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_upload_requires_login(client):
    resp = client.post(
        "/api/v1/files",
        files={"file": ("a.png", PNG_BYTES, "image/png")},
        data={"usage": "credential"},
    )
    assert resp.status_code == 401


def test_upload_ok_and_invalid_type(client, auth_headers):
    resp = client.post(
        "/api/v1/files",
        headers=auth_headers,
        files={"file": ("cert.png", PNG_BYTES, "image/png")},
        data={"usage": "credential"},
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["url"].startswith("/uploads/")
    assert data["isPrivate"] is False

    resp = client.post(
        "/api/v1/files",
        headers=auth_headers,
        files={"file": ("x.txt", b"hello", "text/plain")},
        data={"usage": "credential"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "FILE_TYPE_INVALID"
