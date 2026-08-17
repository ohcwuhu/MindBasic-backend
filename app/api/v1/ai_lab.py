"""
多模态音频分析路由（融合版）
============================
【功能】
  接收前端上传的完整段音频 + SocketIO sid + 录音时间戳，
  并发执行三路分析后融合：
    ① SenseVoice ASR（+ emo 辅助信号）
    ② emotion2vec+ 语调情感
    ③ 文本情感（零样本，依赖 ① 的文本输出）
  最后从 FacialBuffer 提取录音时段内的面部帧序列，
  与三路分析结果进行多模态融合，返回结构化 JSON。

【接口】
  POST /api/analyze_audio
    Form 字段：file(必填), sid, record_start_ts, record_end_ts
  GET  /api/analyze_audio/config_check
  POST /api/analyze_audio/warmup
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
import traceback
from typing import Any

_log = logging.getLogger("ai-lab")

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.deps import get_current_user, require_role
from app.models.user import User
from app.services.ai_lab import config

# ============================================================
#  导入常驻服务
# ============================================================
from app.services.ai_lab import sensevoice_service
from app.services.ai_lab import emotion2vec_service
from app.services.ai_lab import text_emotion_service
from app.services.ai_lab import opensmile_service       # 后备：emotion2vec+ 加载失败时降级
from app.services.ai_lab import fusion_service
from app.services.ai_lab import facial_buffer

_SV = sensevoice_service
_EV = emotion2vec_service
_TE = text_emotion_service
_OS = opensmile_service
_FS = fusion_service

router = APIRouter(prefix="/api", tags=["multimodal-audio"])


# ============================================================
#  进程内调用包装（统一错误 → (result_dict, error_str, elapsed)）
# ============================================================
def _call_sensevoice(audio_path: str) -> tuple[dict | None, str | None, float]:
    t0 = time.time()
    try:
        result = _SV.transcribe(audio_path)
        return result, None, round(time.time() - t0, 3)
    except Exception as e:
        tb = traceback.format_exc()[-1500:]
        return None, f"{type(e).__name__}: {e}\n{tb}", round(time.time() - t0, 3)


def _call_emotion2vec(audio_path: str) -> tuple[dict | None, str | None, float]:
    """语调情感分析：优先 emotion2vec+，失败则降级到 OpenSMILE。"""
    t0 = time.time()
    try:
        result = _EV.analyze(audio_path)
        result["_fallback_used"] = False
        return result, None, round(time.time() - t0, 3)
    except Exception as e1:
        # emotion2vec+ 失败，尝试 OpenSMILE 降级
        try:
            result = _OS.analyze(audio_path)
            # OpenSMILE 返回格式不同，需转换为统一格式
            unified = _convert_opensmile_result(result)
            unified["_fallback_used"] = True
            unified["_fallback_error"] = f"{type(e1).__name__}: {e1}"
            return unified, None, round(time.time() - t0, 3)
        except Exception as e2:
            tb = traceback.format_exc()[-1500:]
            return None, f"emotion2vec: {e1} | opensmile: {e2}\n{tb}", round(time.time() - t0, 3)


def _call_text_emotion(text: str) -> tuple[dict | None, str | None, float]:
    t0 = time.time()
    try:
        result = _TE.analyze(text)
        return result, None, round(time.time() - t0, 3)
    except Exception as e:
        tb = traceback.format_exc()[-1500:]
        return None, f"{type(e).__name__}: {e}\n{tb}", round(time.time() - t0, 3)


def _convert_opensmile_result(os_result: dict) -> dict:
    """将 OpenSMILE 结果转换为与 emotion2vec+ 一致的统一格式。"""
    # OpenSMILE 的 emotion_scores 是 8 类，需映射到统一 7 类
    UNIFIED = ["happy", "sad", "angry", "surprised", "fearful", "disgusted", "neutral"]
    OS_MAP = {
        "neutral": "neutral", "happy": "happy", "sad": "sad", "angry": "angry",
        "fearful": "fearful", "surprised": "surprised", "disgusted": "disgusted",
        "bored": "sad",  # bored 归入 sad
    }
    os_scores = os_result.get("emotion_scores", {})
    probs = {label: 0.0 for label in UNIFIED}
    for os_label, score in os_scores.items():
        unified = OS_MAP.get(os_label, "neutral")
        probs[unified] += float(score)
    # 归一化
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}

    dominant = max(probs, key=probs.get)
    return {
        "emotion": dominant,
        "emotion_cn": _FS.EMOTION_CN.get(dominant, dominant),
        "confidence": round(probs[dominant], 3),
        "probabilities": {k: round(v, 3) for k, v in probs.items()},
        "method": "opensmile_fallback (eGeMAPSv02)",
    }


# ============================================================
#  HTTP 接口：/api/analyze_audio
# ============================================================
@router.post("/analyze_audio")
async def analyze_audio(
    user: User = Depends(get_current_user),
    file: UploadFile = File(...),
    sid: str = Form(""),
    record_start_ts: float = Form(0.0),   # 前端 Date.now() 毫秒级
    record_end_ts: float = Form(0.0),     # 前端 Date.now() 毫秒级
) -> JSONResponse:
    """
    多模态音频分析接口（融合版）。

    请求：multipart/form-data
        file: 音频文件（webm/wav/mp3 等）
        sid: SocketIO 客户端 ID（用于查询面部缓冲）
        record_start_ts: 录音开始时间戳（毫秒，Date.now()）
        record_end_ts: 录音结束时间戳（毫秒，Date.now()）

    响应：融合后的完整 JSON（见方案文档第 6 章）
    """
    # ---------- 1) 保存上传音频到临时文件 ----------
    tmp_audio_path: str | None = None
    try:
        suffix = ".webm"
        fname_lower = (file.filename or "").lower()
        if fname_lower.endswith(".wav"):
            suffix = ".wav"
        elif fname_lower.endswith(".mp3"):
            suffix = ".mp3"
        elif fname_lower.endswith((".m4a", ".flac", ".ogg")):
            suffix = os.path.splitext(fname_lower)[1]

        tmp_dir = str(config.TEMP_AUDIO_DIR) if config.TEMP_AUDIO_DIR else None
        tf = tempfile.NamedTemporaryFile(
            suffix=suffix, prefix="relmind_audio_", dir=tmp_dir, delete=False,
        )
        tmp_audio_path = tf.name
        try:
            total_size = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tf.write(chunk)
                total_size += len(chunk)
                if total_size > 50 * 1024 * 1024:
                    raise HTTPException(
                        status_code=413,
                        detail="音频文件过大（> 50MB），请分段后重试或缩短录音时长。",
                    )
        finally:
            tf.close()

        total_start = time.time()

        # ---------- 2) 并发调用 SenseVoice ASR + emotion2vec+ ----------
        asr_result: dict | None = None
        asr_error: str | None = None
        asr_elapsed: float | None = None

        voice_result: dict | None = None
        voice_error: str | None = None
        voice_elapsed: float | None = None

        async with anyio.create_task_group() as tg:
            async def _run_asr():
                nonlocal asr_result, asr_error, asr_elapsed
                asr_result, asr_error, asr_elapsed = await anyio.to_thread.run_sync(
                    _call_sensevoice, tmp_audio_path,
                )

            async def _run_voice():
                nonlocal voice_result, voice_error, voice_elapsed
                voice_result, voice_error, voice_elapsed = await anyio.to_thread.run_sync(
                    _call_emotion2vec, tmp_audio_path,
                )

            tg.start_soon(_run_asr)
            tg.start_soon(_run_voice)

        # ---------- 3) 文本情感分析（依赖 ASR 结果）----------
        text_result: dict | None = None
        text_error: str | None = None
        text_elapsed: float | None = None

        if asr_result and asr_result.get("text"):
            text_result, text_error, text_elapsed = await anyio.to_thread.run_sync(
                _call_text_emotion, asr_result["text"],
            )
        else:
            text_result = _TE._empty_result("")
            text_error = "ASR 无文本输出，跳过文本情感分析"

        # ---------- 4) SenseVoice emo 辅助信号 ----------
        sv_emo = "neutral"
        if asr_result:
            sv_emo = asr_result.get("emo", "neutral")
        sv_emo_result = {"emotion": sv_emo, "source": "SenseVoice emo"}

        # ---------- 5) 多模态融合 ----------
        # 时间戳转换：前端毫秒 → 后端秒（与 facial_buffer 的 time.time() 对齐）
        # 注意：前端 Date.now() 与后端 time.time() 可能存在时钟偏差，
        #       融合时在时间窗口前后各扩展 2 秒以容忍偏差。
        t_start_sec = record_start_ts / 1000.0 if record_start_ts else 0.0
        t_end_sec = record_end_ts / 1000.0 if record_end_ts else 0.0

        # 构造空结果占位（防止某路分析失败时融合崩溃）
        if text_result is None:
            text_result = _TE._empty_result(asr_result.get("text", "") if asr_result else "")
        if voice_result is None:
            voice_result = _EV._empty_result()
            voice_result["method"] = "failed"

        fusion_result = _FS.fuse(
            text_result=text_result,
            voice_result=voice_result,
            sv_emo_result=sv_emo_result,
            sid=sid,
            record_start_ts=t_start_sec,
            record_end_ts=t_end_sec,
        )

        total_elapsed = round(time.time() - total_start, 3)

        # ---------- 6) 构造响应 ----------
        # 判断整体状态
        success_count = int(asr_result is not None) + int(voice_result is not None) + int(text_result is not None and text_error is None)
        if success_count == 3:
            status = "ok"
        elif success_count >= 1:
            status = "partial_success"
        else:
            status = "failed"

        response_body: dict[str, Any] = {
            "status": status,
            "transcription": {
                "text": asr_result.get("text", "") if asr_result else "",
                "language": asr_result.get("language", "zh") if asr_result else "zh",
                "duration_seconds": asr_result.get("duration_seconds", 0.0) if asr_result else 0.0,
            },
            "text_emotion": text_result,
            "voice_emotion": {
                **voice_result,
                "sv_cross_check": {
                    "emotion": sv_emo,
                    "agree": sv_emo == voice_result.get("emotion") if voice_result else False,
                    "source": "SenseVoice emo",
                },
            },
            "facial_emotion": fusion_result["facial_emotion"],
            "fusion": fusion_result["fusion"],
            "errors": {
                "asr_error": asr_error,
                "voice_emotion_error": voice_error,
                "text_emotion_error": text_error,
            },
            "timing": {
                "total_seconds": total_elapsed,
                "asr_seconds": asr_elapsed,
                "voice_emotion_seconds": voice_elapsed,
                "text_emotion_seconds": text_elapsed,
                "facial_buffer_frames": fusion_result["facial_emotion"]["frame_count"],
            },
            "server_info": {
                "sid": sid,
                "record_start_ts": t_start_sec,
                "record_end_ts": t_end_sec,
                "audio_size_bytes": total_size,
                "models_loaded": {
                    "sensevoice": _SV.get_status().get("loaded"),
                    "emotion2vec": _EV.get_status().get("loaded"),
                    "text_emotion": _TE.get_status().get("loaded"),
                    "opensmile": _OS.get_status().get("loaded"),
                },
                "voice_fallback_used": voice_result.get("_fallback_used", False) if voice_result else False,
            },
        }

        # 清除内部字段（不对外暴露）
        if voice_result:
            voice_result.pop("_fallback_used", None)
            voice_result.pop("_fallback_error", None)

        return JSONResponse(content=response_body)

    finally:
        # ---------- 7) 清理临时音频 ----------
        if tmp_audio_path and os.path.exists(tmp_audio_path):
            try:
                os.remove(tmp_audio_path)
            except Exception:
                pass


# ============================================================
#  辅助接口：/api/analyze_audio/config_check
# ============================================================
@router.get("/analyze_audio/config_check")
async def config_check(
    admin: User = Depends(require_role("ADMIN")),
) -> JSONResponse:
    """检查所有常驻服务的加载状态。"""
    return JSONResponse(content={
        "sensevoice": _SV.get_status(),
        "emotion2vec": _EV.get_status(),
        "text_emotion": _TE.get_status(),
        "opensmile": _OS.get_status(),
        "facial_buffer": facial_buffer.get_status(),
        "python": config.CURRENT_PYTHON,
        "mode": "IN-PROCESS ONLY (integrated, multimodal fusion)",
    })


# ============================================================
#  辅助接口：/api/analyze_audio/warmup
# ============================================================
@router.post("/analyze_audio/warmup")
async def warmup(
    admin: User = Depends(require_role("ADMIN")),
) -> JSONResponse:
    """
    主动预热所有模型（SenseVoice + emotion2vec+ + 文本情感 + OpenSMILE）。
    建议在后端启动后调用一次，避免第一个用户请求等待。
    """
    results = {}
    for name, svc in [("sensevoice", _SV), ("emotion2vec", _EV),
                       ("text_emotion", _TE), ("opensmile", _OS)]:
        try:
            svc.warmup()
            results[name] = "ok"
        except Exception as e:
            results[name] = f"failed: {e}"

    return JSONResponse(content={
        "warmup_results": results,
        "note": "通过 /api/analyze_audio/config_check 查看各模型 loaded 状态",
    })


# ================================================================
#  视频通话音频上传（给 socket 管线使用，避免大 base64 传输乱序）
# ================================================================
import uuid
import shutil

_VC_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "vc_uploads")
os.makedirs(_VC_UPLOAD_DIR, exist_ok=True)

@router.post("/vc_audio_upload")
async def vc_audio_upload(
    user: User = Depends(get_current_user),
    file: UploadFile = File(...),
    sid: str = Form(""),
) -> JSONResponse:
    """
    视频通话音频上传接口。
    前端将 Blob 音频文件通过 HTTP POST 上传，后端保存到临时目录并返回 file_id。
    然后前端通过 socket 发送 vc_audio_end 时携带 file_id，
    后端管线直接从磁盘读取文件，避免 base64 分片在 socket 中乱序。
    """
    try:
        if not file.filename:
            return JSONResponse(content={"error": "没有文件"}, status_code=400)

        file_id = uuid.uuid4().hex
        suffix = os.path.splitext(file.filename)[1] or ".webm"
        if suffix not in (".webm", ".webma", ".ogg", ".mp3", ".wav", ".opus"):
            suffix = ".webm"
        dest_path = os.path.join(_VC_UPLOAD_DIR, f"{file_id}{suffix}")

        with open(dest_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        file_size = os.path.getsize(dest_path)
        _log.info("[VC Upload] sid=%s, file_id=%s, size=%dB, path=%s",
                  sid, file_id, file_size, dest_path)

        return JSONResponse(content={
            "ok": True,
            "file_id": file_id,
            "file_path": dest_path,
            "file_size": file_size,
        })
    except Exception as e:
        _log.error("[VC Upload] 上传失败: %s", e, exc_info=True)
        return JSONResponse(content={"error": str(e)}, status_code=500)
