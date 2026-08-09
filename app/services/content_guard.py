"""内容合规校验：心理教练平台禁用词（与 PRD 文案规范一致）。"""

from app.core.exceptions import AppError

BANNED_WORDS = [
    "治疗",
    "治愈",
    "病症",
    "心理疾病",
    "精神疾病",
    "诊断",
    "处方",
]


def check_banned_words(*texts: str | None) -> None:
    found = sorted({word for word in BANNED_WORDS if any(text and word in text for text in texts)})
    if found:
        raise AppError(400, "VALIDATION_ERROR", f"内容包含禁用词：{'、'.join(found)}")
