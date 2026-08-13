"""AI 实验室（RelMind 复刻）：无重型模型时降级为 mock，接口可用。"""

import base64

from app.services.ai_lab import audio_engine, emotion_engine


def test_analyze_frame_mock():
    frame = "data:image/jpeg;base64," + base64.b64encode(b"fake-jpeg-bytes").decode()
    result = emotion_engine.analyze_frame(frame)
    assert result["source"] == "mock"
    assert 0 <= result["score"] <= 100
    assert result["level"] in ("ENGAGED", "NEUTRAL", "BORING")
    assert result["alert"] == (result["score"] < 40)
    assert result["emotions"]


def test_analyze_audio_mock():
    result = audio_engine.analyze_audio(b"fake-webm-bytes")
    assert result["status"] == "mock"
    assert result["transcription"]["text"]
    assert result["voice_features"]["primary_emotion"]


def test_ai_lab_endpoints(client, auth_headers):
    resp = client.get("/api/v1/ai-lab/config-check", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "frameEmotion" in data and "audio" in data

    files = {"file": ("record.webm", b"fake-webm-bytes", "audio/webm")}
    resp = client.post("/api/v1/ai-lab/analyze-audio", headers=auth_headers, files=files)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] in ("mock", "partial_success")
    assert body["transcription"]["text"]

    resp = client.post("/api/v1/ai-lab/analyze-audio", headers=auth_headers, files={"file": ("x.webm", b"", "audio/webm")})
    assert resp.status_code == 400


def test_ai_lab_requires_login(client):
    assert client.get("/api/v1/ai-lab/config-check").status_code == 401
