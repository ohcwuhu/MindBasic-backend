"""实时人脸情绪识别引擎（DeepFace 惰性加载，缺失时降级为确定性模拟）。"""

import hashlib
import os
import random

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

MODEL_AVAILABLE = False
_analyze = None


def _load_model() -> bool:
    """惰性加载 DeepFace；失败不阻塞服务启动。"""
    global MODEL_AVAILABLE, _analyze
    try:
        from deepface import DeepFace

        _analyze = DeepFace.analyze
        MODEL_AVAILABLE = True
    except Exception:  # noqa: BLE001 - 模型缺失时降级
        MODEL_AVAILABLE = False
    return MODEL_AVAILABLE


EMOTION_SCORES = {
    "happy": 100,
    "surprise": 75,
    "neutral": 55,
    "fear": 30,
    "sad": 20,
    "angry": 15,
    "disgust": 10,
}


def _mock_emotions(img_base64: str) -> dict[str, float]:
    seed = int(hashlib.md5(img_base64.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    base = rng.choice(["happy", "neutral", "neutral", "sad", "surprise"])
    emotions: dict[str, float] = {"happy": 0, "surprise": 0, "neutral": 0, "fear": 0, "sad": 0, "angry": 0, "disgust": 0}
    emotions[base] = rng.uniform(0.5, 0.9)
    for key in emotions:
        if key != base:
            emotions[key] = rng.uniform(0.01, 0.2)
    return emotions


def analyze_frame(img_base64: str, students: int | None = None) -> dict:
    """返回与 RelMind 协议一致的 emotion_result 载荷。"""
    if not MODEL_AVAILABLE and not _load_model():
        emotions = _mock_emotions(img_base64)
        source = "mock"
    else:
        try:
            result = _analyze(img_base64=img_base64, actions=["emotion"], detector_backend="mtcnn", enforce_detection=False)
            emotions = result[0]["emotion"]
            source = "model"
        except Exception:  # noqa: BLE001 - 单帧失败降级
            emotions = _mock_emotions(img_base64)
            source = "mock"

    detected = {k: v for k, v in emotions.items() if v > 0}
    if not detected:
        detected = {"neutral": 1.0}
    total = sum(detected.values())
    emotions_norm = {k: round(v / total, 4) for k, v in detected.items()}
    dominant = max(emotions_norm, key=emotions_norm.get)
    score = round(EMOTION_SCORES.get(dominant, 55))
    level = "ENGAGED" if score >= 70 else "NEUTRAL" if score >= 40 else "BORING"
    return {
        "score": score,
        "students": students if students is not None else (1 if source == "model" else 3),
        "alert": score < 40,
        "level": level,
        "emotions": emotions_norm,
        "source": source,
    }
