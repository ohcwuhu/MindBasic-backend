"""AI 实验（RelMind 复刻）：实时情绪识别状态 + 音频分析。"""

import logging

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_async_db, get_current_user
from app.api.response import ok
from app.core.config import settings
from app.core.exceptions import AppError
from app.models.user import User
from app.services.ai_lab import audio_engine, emotion_engine

logger = logging.getLogger("mindbasic")

router = APIRouter(prefix="/ai-lab", tags=["ai-lab"])


def _check_enabled() -> None:
    if not settings.ai_lab_enabled:
        raise AppError(404, "NOT_FOUND", "AI 实验室未开启")


@router.get("/config-check")
async def config_check(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: User = Depends(get_current_user),
) -> dict:
    _check_enabled()
    return ok(
        {
            "frameEmotion": {"model": emotion_engine.MODEL_AVAILABLE or emotion_engine._load_model()},
            "audio": {
                "asr": audio_engine.ASR_AVAILABLE,
                "voice": audio_engine.VOICE_AVAILABLE,
            },
        },
        trace_id=request.state.trace_id,
    )


@router.post("/analyze-audio")
async def analyze_audio_endpoint(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
    _: User = Depends(get_current_user),
) -> dict:
    _check_enabled()
    data = await file.read()
    if not data:
        raise AppError(400, "VALIDATION_ERROR", "音频内容为空")
    if len(data) > settings.ai_lab_audio_max_mb * 1024 * 1024:
        raise AppError(400, "FILE_TOO_LARGE", f"音频大小不能超过 {settings.ai_lab_audio_max_mb}MB")
    result = await run_in_threadpool(audio_engine.analyze_audio, data)
    return ok(result, trace_id=request.state.trace_id)


@router.post("/warmup")
async def warmup(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: User = Depends(get_current_user),
) -> dict:
    _check_enabled()
    await run_in_threadpool(audio_engine._load_asr)
    await run_in_threadpool(audio_engine._load_voice)
    return ok(
        {
            "asr": audio_engine.ASR_AVAILABLE,
            "voice": audio_engine.VOICE_AVAILABLE,
        },
        trace_id=request.state.trace_id,
    )
