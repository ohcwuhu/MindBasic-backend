"""SocketIO 实时情绪识别（与 RelMind upload_frame 协议对齐）。"""

import logging
import time
import asyncio

from app.core.config import settings
from app.services.ai_lab.emotion_engine import analyze_frame

logger = logging.getLogger("mindbasic")


def register_socket_events(sio) -> None:
    @sio.event
    async def upload_frame(sid: str, data: dict):
        started = time.perf_counter()
        try:
            img_base64 = (data or {}).get("imgBase64", "")
            if not img_base64:
                raise ValueError("imgBase64 为空")
            result = await asyncio.to_thread(analyze_frame, img_base64)
            await sio.emit(
                "emotion_result",
                {
                    "timestamp": time.strftime("%H:%M:%S"),
                    **result,
                    "processing_time_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                to=sid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("emotion frame failed: %s", exc)
            await sio.emit("emotion_error", {"error": "analyze_failed", "message": str(exc)}, to=sid)
