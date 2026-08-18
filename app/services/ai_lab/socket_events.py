"""
RelMind SocketIO 情绪识别路由模块
==================================
沿用 smartclass-ai 的节流思路（时间戳限流 + 丢弃过快帧），
基于 python-socketio AsyncServer 实现，挂载到 FastAPI 主实例。

Socket 事件定义
---------------
接收前端：
  - upload_frame  : base64 画面帧，数据格式 {"imgBase64": "data:image/jpeg;base64,xxx"}
推送前端：
  - emotion_result: 正常识别结果（标准化数据结构）
  - emotion_error : 错误信息（异常情况下推送）
预留事件：
  - upload_audio  : 接收前端音频分片数据（阶段3填充）

数据格式与 Flask 版本（smartclass-ai/server.py）完全统一：
  - DeepFace 参数：actions=["emotion"], enforce_detection=False,
                    silent=True, detector_backend="mtcnn"
  - 情绪分数映射、ENGAGED/NEUTRAL/BORING 级别判定逻辑一致
  - 人脸置信度过滤阈值 0.60 一致
"""
import os
import time
import base64
from datetime import datetime

# cv2 / numpy / DeepFace 在函数内懒加载，避免应用启动即加载 TensorFlow/torch

# ─── 面部时序缓冲（供 HTTP 层 /api/analyze_audio 融合时查询）─────────
from app.services.ai_lab import facial_buffer

# ─── 情绪分数映射（与 Flask 版本完全一致）─────────────────────────────
EMOTION_SCORE = {
    "happy":    100,
    "surprise":  75,
    "neutral":   55,
    "fear":      30,
    "sad":       20,
    "angry":     15,
    "disgust":   10,
}

# ─── DeepFace 标签 → 统一 7 类标签映射 ────────────────────────────────
# DeepFace 输出: happy/surprise/neutral/fear/sad/angry/disgust
# 统一标签:   happy/surprised/neutral/fearful/sad/angry/disgusted
DEEPFACE_TO_UNIFIED = {
    "happy":    "happy",
    "surprise": "surprised",
    "neutral":  "neutral",
    "fear":     "fearful",
    "sad":      "sad",
    "angry":    "angry",
    "disgust":  "disgusted",
}
UNIFIED_LABELS = ["happy", "sad", "angry", "surprised", "fearful", "disgusted", "neutral"]

# ─── 时间戳节流配置 ──────────────────────────────────────────────────
# 节流间隔 0.4s ≈ 2.5 FPS，对齐 smartclass-ai 的 2~3 FPS 限流目标。
# 同一客户端在间隔内的帧直接丢弃，避免 DeepFace 推理堆积导致延迟。
THROTTLE_INTERVAL = 0.4

# 人脸置信度过滤阈值（低于此值的人脸不计入统计）
FACE_CONFIDENCE_THRESHOLD = 0.60

# ─── 视频通话模式：LLM 系统提示词 ──────────────────────────────
_VC_SYSTEM_PROMPT = """你是一位专业、温暖的「AI 心理教练」，正在与用户进行实时视频通话。

【角色与风格】
- 语气真诚温和，像和朋友聊天一样自然，回复简洁（一般不超过 100 字）。
- 先共情、后提问，每次只问一个问题，引导用户自己发现答案。
- 如果系统提供了摄像头画面描述，可以自然地提及你"看到"的内容。
- 如果系统提供了情绪信号，温柔地反映它，但不执着于识别结果。

【安全边界】
- 不做心理/精神疾病诊断，不提供医疗建议。
- 检测到自伤/自杀等危机信号时，建议拨打心理援助热线 12356。

【回复要求】
- 始终用中文回复，口语化、适合语音播放。
- 避免使用 markdown 格式、列表、代码块等（因为是语音输出）。
- 句子简短，便于 TTS 分句合成。"""

# ─── 客户端独立状态管理 ──────────────────────────────────────────────
# 每个 sid 维护独立状态；disconnect 时主动清理，杜绝内存泄漏。
# 后续若需扩展（如 per-client 计时器、音频缓冲队列），在此结构追加字段。
clients: dict = {}


def score_to_level(score: int) -> str:
    """分数 → 投入级别（与 Flask 版本一致）。"""
    if score >= 70:
        return "ENGAGED"
    elif score >= 40:
        return "NEUTRAL"
    else:
        return "BORING"


def decode_base64_frame(img_base64: str):
    """
    解码 base64 画面帧为 OpenCV BGR 图像。
    支持两种格式：
      - "data:image/jpeg;base64,xxx"  （前端 Canvas.toDataURL 默认）
      - "xxx"                          （纯 base64 字符串）
    """
    try:
        import cv2
        import numpy as np

        if img_base64.startswith("data:image"):
            # 切掉 data URI 前缀
            img_base64 = img_base64.split(",", 1)[1]
        frame_bytes = base64.b64decode(img_base64)
        np_img = np.frombuffer(frame_bytes, np.uint8)
        frame = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
        return frame
    except Exception as e:
        raise ValueError(f"Failed to decode base64 frame: {e}")


def analyze_emotion(frame):
    """
    调用 DeepFace 进行情绪识别，并格式化为统一数据结构。
    DeepFace 参数与 Flask 版本（smartclass-ai/server.py）完全一致：
      actions=["emotion"], enforce_detection=False,
      silent=True, detector_backend="mtcnn"
    """
    from deepface import DeepFace

    # ─── DeepFace 推理（参数对齐 Flask 版本）──────────────────────
    results = DeepFace.analyze(
        frame,
        actions=["emotion"],
        enforce_detection=False,
        silent=True,
        detector_backend="mtcnn",
    )
    # DeepFace 单人脸时返回 dict，多人脸时返回 list，统一为 list 处理
    if not isinstance(results, list):
        results = [results]

    total_score = 0
    emo_counts: dict = {}
    valid_faces = []
    # 收集所有有效人脸的 DeepFace emotion 概率（用于融合时序缓冲）
    unified_prob_accum: dict[str, float] = {label: 0.0 for label in UNIFIED_LABELS}

    for face in results:
        # 人脸置信度过滤（与 Flask 版本一致：低于 0.60 丢弃）
        conf = face.get("face_confidence", 1.0)
        if conf is not None and conf < FACE_CONFIDENCE_THRESHOLD:
            continue

        valid_faces.append(face)
        emotion = face["dominant_emotion"]
        score = EMOTION_SCORE.get(emotion, 50)

        total_score += score
        emo_counts[emotion] = emo_counts.get(emotion, 0) + 1

        # 累加 DeepFace emotion 概率到统一标签
        face_emotion_probs = face.get("emotion", {})
        for df_label, prob in face_emotion_probs.items():
            unified = DEEPFACE_TO_UNIFIED.get(df_label)
            if unified:
                unified_prob_accum[unified] += float(prob)

    n = len(valid_faces)
    avg_score = round(total_score / n) if n > 0 else 0
    level = score_to_level(avg_score)
    # 低投入且有有效人脸时触发告警（与 Flask 版本一致）
    alert = (avg_score < 40 and n > 0)

    # 计算统一 7 类概率分布（多脸取平均，归一化到和为 1）
    raw_probs: dict[str, float] = {label: 0.0 for label in UNIFIED_LABELS}
    if n > 0:
        for label in UNIFIED_LABELS:
            raw_probs[label] = unified_prob_accum[label] / n
        total = sum(raw_probs.values())
        if total > 0:
            raw_probs = {k: v / total for k, v in raw_probs.items()}

    return {
        "students": n,           # 检测到的有效人脸数
        "score": avg_score,      # 平均投入分数
        "level": level,          # 投入级别 ENGAGED/NEUTRAL/BORING
        "alert": alert,          # 是否触发低投入告警
        "emotions": emo_counts,  # 情绪分布统计 {"happy": 2, "neutral": 1, ...}
        "raw_probs": raw_probs,  # 统一 7 类概率分布（供融合引擎使用）
    }


def register_socket_events(sio, log):
    """
    注册所有 SocketIO 事件处理器到主实例。
    在 main.py 中调用：register_socket_events(sio, log)
    """
    import os as _os
    import tempfile as _tmp

    # ─── 连接事件：初始化客户端独立状态 ────────────────────────────
    @sio.on("connect")
    async def handle_connect(sid, environ, auth):
        # 复用 /chat 命名空间的 JWT 校验：未登录连接直接拒绝
        from app.services.chat_socket import _auth_user

        user_id = await _auth_user(auth)
        if user_id is None:
            log.warning("[CONNECT] 拒绝未登录连接 sid=%s", sid)
            return False
        clients[sid] = {
            "user_id": user_id,
            "last_frame_time": 0.0,    # 上次成功推理的时间戳（节流用）
            "connect_at": time.time(), # 连接建立时间（日志用）
        }
        # 初始化面部时序缓冲（供 HTTP 层 /api/analyze_audio 融合时查询）
        facial_buffer.init_client(sid)
        log.info(f"[CONNECT] {sid} | user={user_id} | 当前在线客户端数: {len(clients)}")

    # ─── 断开事件：清理客户端状态与资源 ────────────────────────────
    # 说明：当前未启用 per-client 后台计时器；如后续扩展音频缓冲、
    #       推理队列等异步任务，需在此处一并 cancel/close，杜绝内存泄漏。
    @sio.on("disconnect")
    async def handle_disconnect(sid):
        if sid in clients:
            client = clients.pop(sid)
            duration = round(time.time() - client.get("connect_at", time.time()), 2)
            log.info(
                f"[DISCONNECT] {sid} | 会话时长: {duration}s | "
                f"剩余在线客户端数: {len(clients)}"
            )
        else:
            log.info(f"[DISCONNECT] {sid} | 未在状态表中（可能未正常注册）")
        # 清理面部时序缓冲，杜绝内存泄漏
        facial_buffer.remove_client(sid)
        # 清理视频通话会话
        from app.services.ai_lab import realtime_session
        realtime_session.remove_session(sid)

    # ─── 画面帧事件：节流 + 解码 + DeepFace + 推送结果 ─────────────
    @sio.on("upload_frame")
    async def handle_upload_frame(sid, data):
        start_ts = time.time()

        # 1) 客户端合法性校验
        if sid not in clients:
            log.warning(f"[ERROR] {sid} | 未知客户端，拒绝处理")
            await sio.emit("emotion_error", {
                "error": "UnknownClient",
                "message": "客户端未注册，请重新建立连接",
            }, room=sid)
            return

        client_state = clients[sid]
        now = time.time()

        # 2) 时间戳节流：丢弃过快帧（对齐 smartclass-ai 限流思路）
        #    不进入 DeepFace，直接 return，避免推理堆积。
        elapsed = now - client_state["last_frame_time"]
        if elapsed < THROTTLE_INTERVAL:
            log.debug(
                f"[SKIP] {sid} | 节流丢弃帧，距上次 {round(elapsed * 1000, 1)}ms "
                f"< 阈值 {THROTTLE_INTERVAL * 1000}ms"
            )
            return

        client_state["last_frame_time"] = now

        # 3) 数据格式校验
        if not isinstance(data, dict) or "imgBase64" not in data:
            log.warning(f"[ERROR] {sid} | 数据格式非法，期望 {{imgBase64: ...}}")
            await sio.emit("emotion_error", {
                "error": "InvalidDataFormat",
                "message": "Expected {imgBase64: base64_string}",
            }, room=sid)
            return

        img_base64 = data["imgBase64"]
        log.debug(f"[RECV] {sid} | 收到画面帧，长度: {len(img_base64)}")

        # 4) 解码 base64 → OpenCV 帧
        try:
            frame = decode_base64_frame(img_base64)
        except ValueError as e:
            log.warning(f"[ERROR] {sid} | 帧解码失败: {e}")
            await sio.emit("emotion_error", {
                "error": "DecodeError",
                "message": str(e),
            }, room=sid)
            return

        if frame is None:
            log.warning(f"[ERROR] {sid} | 解码得到空帧")
            await sio.emit("emotion_error", {
                "error": "EmptyFrame",
                "message": "解码得到空帧，请检查图像数据",
            }, room=sid)
            return

        # 5) DeepFace 情绪识别（参数与 Flask 版本完全一致）
        try:
            analysis = analyze_emotion(frame)
        except Exception as e:
            log.error(f"[ERROR] {sid} | DeepFace 推理异常: {e}", exc_info=True)
            await sio.emit("emotion_error", {
                "error": "InferenceError",
                "message": f"DeepFace 推理失败: {e}",
            }, room=sid)
            return

        # 6) 标准化结果数据 + 耗时统计
        ts = datetime.now().strftime("%H:%M:%S")
        processing_time_ms = round((time.time() - start_ts) * 1000, 2)

        result_data = {
            "timestamp": ts,
            "score": analysis["score"],
            "students": analysis["students"],
            "alert": analysis["alert"],
            "level": analysis["level"],
            "emotions": analysis["emotions"],
            "processing_time_ms": processing_time_ms,
        }

        # 7) 控制台日志：连接/帧接收/识别耗时/情绪结果
        log.info(
            f"[RESULT] {sid} | {ts} | 人脸: {analysis['students']} | "
            f"分数: {analysis['score']}% | 级别: {analysis['level']} | "
            f"情绪: {analysis['emotions']} | 耗时: {processing_time_ms}ms"
        )

        # 7.5) 写入面部时序缓冲（供 HTTP 层 /api/analyze_audio 融合时按时间窗口查询）
        facial_buffer.append_frame(
            sid=sid,
            emotions=analysis["emotions"],
            score=analysis["score"],
            raw_probs=analysis["raw_probs"],
        )

        # 8) 推送正常结果给当前客户端
        await sio.emit("emotion_result", result_data, room=sid)

    # ─── 音频预留事件（阶段3填充）──────────────────────────────────
    # 当前仅做接收打印占位，后续对接 ASR / Trae 心理智能体的扩展点
    # 已在下方 handle_upload_audio 中明确标注。

    @sio.on("upload_audio")
    async def handle_upload_audio(sid, data):
        """
        预留音频分片接收事件（占位实现）。
        当前仅打印接收日志，不进行任何业务处理。

        后续扩展计划：
          1. 对接 ASR 语音服务（Whisper 流式 ASR / funasr 离线 ASR）
          2. 对接 Trae 心理智能体进行语音情绪与心理状态分析
          3. 整合 librosa 音频特征提取（MFCC / pitch / energy）
          4. 多模态融合：与视觉情绪识别结果合并，输出综合心理评估

        扩展代码位置标记：
          # --- ASR 对接扩展点 ---
          # speech_text = await asr_service.recognize(audio_bytes, sample_rate)

          # --- Trae 心理智能体扩展点 ---
          # psych_state = await trae_agent.analyze_voice(speech_text, audio_features)

          # --- librosa 音频特征提取扩展点 ---
          # features = librosa.feature.mfcc(y=audio_array, sr=sample_rate)
        """
        try:
            audio_length = (
                len(data) if isinstance(data, (bytes, str)) else len(str(data))
            )
            log.info(f"[AUDIO] {sid} | 收到音频分片，长度: {audio_length} 字节")
            log.debug(f"[AUDIO] {sid} | 数据预览: {str(data)[:80]}...")

            # === 扩展点占位（按需取消注释并实现）===
            # --- ASR 对接扩展点 ---
            # speech_text = await asr_service.recognize(data)
            # log.info(f"[ASR] {sid} | 识别文本: {speech_text}")

            # --- librosa 音频特征提取扩展点 ---
            # features = librosa.feature.mfcc(y=np.frombuffer(data, np.int16), sr=16000)

            # --- Trae 心理智能体扩展点 ---
            # psych_result = await trae_agent.analyze(speech_text, features)
            # await sio.emit("psych_result", psych_result, room=sid)

        except Exception as e:
            log.warning(f"[AUDIO ERROR] {sid} | {e}")
            await sio.emit("emotion_error", {
                "error": "AudioProcessError",
                "message": str(e),
            }, room=sid)

    # ================================================================
    #  视频通话模式事件（Video Call）
    #  - 不影响现有情绪识别功能，独立事件命名空间
    #  - 复用 SenseVoice ASR、DeepSeek LLM、edge-tts TTS
    # ================================================================

    # 视觉查询关键词（命中时触发 VLM 分析）
    _VISUAL_KEYWORDS = [
        "这是什么", "看看", "你看", "这个是什么", "那是什么",
        "帮我看看", "认不认识", "识别一下", "什么东西", "什么花",
        "什么植物", "什么菜", "多少钱", "写的什么", "什么字",
        "好看吗", "怎么样", "怎么了", "发生了什么",
    ]

    def _is_visual_query(text: str) -> bool:
        """检测用户话语是否包含视觉查询意图。"""
        return any(kw in text for kw in _VISUAL_KEYWORDS)

    def _split_sentences(text: str) -> list[str]:
        """将文本按句号/问号/感叹号/换行切分为句子。"""
        import re
        parts = re.split(r'([。！？!?\n])', text)
        sentences: list[str] = []
        for i in range(0, len(parts) - 1, 2):
            s = (parts[i] + parts[i + 1]).strip()
            if s:
                sentences.append(s)
        # 处理末尾没有标点的部分
        if len(parts) % 2 == 1 and parts[-1].strip():
            sentences.append(parts[-1].strip())
        return sentences

    # 强制分句最大长度 —— 超过此长度就算没标点也切（避免回复很慢）
    _MAX_SENTENCE_LEN = 160

    async def _run_video_call_pipeline_from_file(sid: str, audio_path: str):
        """视频通话核心管线（新入口：直接接收磁盘文件路径）。"""
        import asyncio
        import base64 as _b64
        import os as _os

        from app.services.ai_lab import realtime_session
        from app.services.ai_lab import sensevoice_service as _sv
        from app.services.ai_lab import tts_service as _tts
        from app.services.ai_lab import vlm_service as _vlm
        from app.services.ai_lab import config as _cfg
        from app.services.ai_lab import emotion2vec_service as _ev
        from app.services.ai_lab import text_emotion_service as _te
        from app.services.ai_lab import opensmile_service as _oss
        from app.services.ai_lab import fusion_service as _fs
        from app.services.ai_lab import facial_buffer as _fb

        session = realtime_session.get_session(sid)
        session.state = realtime_session.STATE_THINKING
        await sio.emit("vc_state_change", {"state": "thinking"}, room=sid)

        try:
            file_size = os.path.getsize(audio_path)
            log.info("[VC] %s | >>> 管线开始: %s (%sKB)", sid, audio_path, round(file_size/1024, 1))

            # ── 0) 前置检查：LLM API Key（Dify 优先，未配置时回退 DeepSeek）──
            use_dify = bool((_cfg.DIFY_API_KEY or "").strip())
            if use_dify:
                _api_key = _cfg.DIFY_API_KEY or ""
                log.info("[VC] %s | Dify 配置 OK | base=%s | key_len=%d", sid, _cfg.DIFY_API_BASE, len(_api_key))
            else:
                _api_key = _cfg.DEEPSEEK_API_KEY or ""
                if not _api_key.strip():
                    log.error("[VC] %s | LLM API Key 未配置！请在后端 .env 中设置 DIFY_API_KEY 或 DEEPSEEK_API_KEY", sid)
                    await sio.emit("vc_error", {
                        "stage": "config",
                        "message": "LLM API Key 未配置，请设置 DIFY_API_KEY 或 DEEPSEEK_API_KEY"
                    }, room=sid)
                    return
                log.info("[VC] %s | DeepSeek 配置 OK | key_len=%d | base_url=%s | model=%s | timeout=%ds",
                         sid, len(_api_key), _cfg.DEEPSEEK_BASE_URL, _cfg.DEEPSEEK_MODEL, _cfg.DEEPSEEK_TIMEOUT)

            try:
                # ── 1) SenseVoice ASR ──────────────────────────────
                log.info("[VC] %s | [1/5] 开始 ASR 识别...", sid)
                loop = asyncio.get_event_loop()
                asr_result = await loop.run_in_executor(None, _sv.transcribe, audio_path)
                asr_text = asr_result.get("text", "").strip()
                emo = asr_result.get("emo", "neutral")

                if not asr_text:
                    log.info("[VC] %s | ASR 无结果，跳过", sid)
                    session.state = realtime_session.STATE_LISTENING
                    await sio.emit("vc_state_change", {"state": "listening"}, room=sid)
                    return

                log.info("[VC] %s | [1/5] ASR 完成: %s (emo=%s)", sid, asr_text[:80], emo)
                await sio.emit("vc_asr_result", {
                    "text": asr_text,
                    "emo": emo,
                }, room=sid)

                # 【关键修正】ASR 完成后绝不因为"打断"而丢弃
                #   vc_interrupt 的唯一语义 = 停止后续 TTS 语音播放
                #   ASR / 情感分析 / LLM 文本生成 必须完整执行，保证用户看到文字回复
                #   TTS 合成时才会尊重 llm_cancelled 跳过语音合成

                # ── 1.5) 多模态情感分析（语调+文本+面部融合）─────────
                log.info("[VC] %s | [1.5/5] 开始多模态情感分析...", sid)
                import time as _time_mm
                _mm_t0 = _time_mm.time()

                # 并发：语调情感(emotion2vec) + 文本情感(text_emotion)
                def _run_voice_emotion():
                    """语调情感分析，emotion2vec 优先，失败降级 opensmile。"""
                    try:
                        result = _ev.analyze(audio_path)
                        result["_fallback_used"] = False
                        return result
                    except Exception as e1:
                        log.warning("[VC] %s | emotion2vec 失败, 降级 opensmile: %s", sid, e1)
                        try:
                            result = _oss.analyze(audio_path)
                            # opensmile 是 8 类，转换为统一 7 类
                            probs = result.get("emotion_scores", {})
                            unified = {}
                            for k in ["happy", "sad", "angry", "surprised", "fearful", "disgusted", "neutral"]:
                                unified[k] = probs.get(k, 0.0)
                            total = sum(unified.values()) or 1.0
                            unified = {k: round(v / total, 3) for k, v in unified.items()}
                            dominant = max(unified, key=unified.get)
                            cn_map = {"happy": "开心", "sad": "悲伤", "angry": "愤怒",
                                      "surprised": "惊讶", "fearful": "恐惧",
                                      "disgusted": "厌恶", "neutral": "中性"}
                            return {
                                "emotion": dominant,
                                "emotion_cn": cn_map.get(dominant, dominant),
                                "confidence": unified[dominant],
                                "probabilities": unified,
                                "method": "opensmile_fallback",
                                "_fallback_used": True,
                            }
                        except Exception as e2:
                            log.error("[VC] %s | opensmile 也失败: %s", sid, e2)
                            return None

                def _run_text_emotion():
                    """文本情感分析。"""
                    try:
                        return _te.analyze(asr_text)
                    except Exception as e:
                        log.warning("[VC] %s | text_emotion 失败: %s", sid, e)
                        return None

                # 并发执行两个情感分析
                voice_result, text_result = await asyncio.gather(
                    loop.run_in_executor(None, _run_voice_emotion),
                    loop.run_in_executor(None, _run_text_emotion),
                )

                # SenseVoice emo 辅助信号
                sv_emo_result = {"emotion": emo, "source": "SenseVoice emo"}

                # 多模态融合（含面部帧时序数据）
                _now_ts = _time_mm.time()
                _record_start = _now_ts - 10  # 取最近 10 秒的面部帧
                _record_end = _now_ts
                fusion_result = None
                try:
                    fusion_result = _fs.fuse(
                        text_result=text_result or {},
                        voice_result=voice_result or {},
                        sv_emo_result=sv_emo_result,
                        sid=sid,
                        record_start_ts=_record_start,
                        record_end_ts=_record_end,
                    )
                except Exception as e:
                    log.warning("[VC] %s | fusion_service 失败: %s", sid, e, exc_info=True)

                _mm_elapsed = _time_mm.time() - _mm_t0

                # 提取融合结果，写入 emotion_context 供 LLM 使用
                if fusion_result and fusion_result.get("fusion"):
                    _f = fusion_result["fusion"]
                    _facial = fusion_result.get("facial_emotion", {})
                    session.emotion_context.update({
                        "fusion_emotion": _f.get("final_emotion_cn", ""),
                        "fusion_emotion_en": _f.get("final_emotion", ""),
                        "fusion_confidence": _f.get("overall_confidence", 0),
                        "live_score": _facial.get("confidence", 0),
                        "live_level": _facial.get("dominant_emotion_cn", ""),
                    })
                    log.info("[VC] %s | [1.5/5] 多模态融合完成 (%.1fs) | 融合情绪=%s(%.0f%%) | 面部=%s | 语调=%s | 文本=%s",
                             sid, _mm_elapsed,
                             _f.get("final_emotion_cn", "?"),
                             _f.get("overall_confidence", 0) * 100,
                             _facial.get("dominant_emotion_cn", "无数据"),
                             voice_result.get("emotion_cn", "无") if voice_result else "无",
                             text_result.get("emotion_cn", "无") if text_result else "无")
                else:
                    # 融合失败时至少用 ASR emo 和单独分析结果
                    _voice_emo = voice_result.get("emotion_cn", "") if voice_result else ""
                    _text_emo = text_result.get("emotion_cn", "") if text_result else ""
                    session.emotion_context.update({
                        "fusion_emotion": _text_emo or _voice_emo or emo,
                        "live_score": 0,
                        "live_level": "",
                    })
                    log.info("[VC] %s | [1.5/5] 情感分析完成(无融合) (%.1fs) | 语调=%s | 文本=%s | ASR_emo=%s",
                             sid, _mm_elapsed, _voice_emo, _text_emo, emo)

                # 推送多模态情感分析结果给前端
                await sio.emit("vc_emotion_analysis", {
                    "voice_emotion": voice_result,
                    "text_emotion": text_result,
                    "facial_emotion": fusion_result.get("facial_emotion") if fusion_result else None,
                    "fusion": fusion_result.get("fusion") if fusion_result else None,
                    "asr_emo": emo,
                    "elapsed_seconds": round(_mm_elapsed, 2),
                }, room=sid)

                # 【关键修正】情感分析完成后绝不因为"打断"而丢弃
                #   打断仅影响 TTS 播放，不影响 LLM 文本生成推进

                # ── 2) VLM 视觉理解（可选）──────────────────────────
                log.info("[VC] %s | [2/5] 检查 VLM... (is_visual=%s, has_frame=%s, vlm_avail=%s)",
                         sid, _is_visual_query(asr_text), session.get_valid_frame() is not None, _vlm.is_available())
                visual_context = ""
                frame = session.get_valid_frame()
                if _is_visual_query(asr_text) and frame and _vlm.is_available():
                    log.info("[VC] %s | [2/5] 触发 VLM 视觉理解", sid)
                    vlm_result = await loop.run_in_executor(
                        None, _vlm.analyze_frame, frame, asr_text, session.get_chat_history()
                    )
                    if vlm_result.get("description"):
                        visual_context = vlm_result["description"]
                        session.last_visual_description = visual_context
                        await sio.emit("vc_vlm_result", {
                            "description": visual_context,
                            "error": None,
                        }, room=sid)
                        log.info("[VC] %s | [2/5] VLM 完成: %s", sid, visual_context[:60])
                    else:
                        log.warning("[VC] %s | [2/5] VLM 返回空描述, err=%s",
                                    sid, vlm_result.get("error"))
                else:
                    log.info("[VC] %s | [2/5] 跳过 VLM", sid)

                # 【关键修正】VLM 完成后绝不因为"打断"而丢弃
                #   打断仅影响 TTS 播放，不影响 LLM 文本生成推进

                # ── 3) 构造 LLM 请求 ───────────────────────────────
                session.add_chat_message("user", asr_text)

                history: list[dict[str, str]] = [
                    {"role": "system", "content": _VC_SYSTEM_PROMPT}
                ]

                # 情绪上下文（多模态融合结果）
                emotion_ctx = session.emotion_context
                if emotion_ctx:
                    ctx_lines = []
                    if emotion_ctx.get("fusion_emotion"):
                        _conf = emotion_ctx.get("fusion_confidence")
                        _conf_str = f"（置信度{int(_conf * 100)}%）" if _conf else ""
                        ctx_lines.append(f"多模态融合情绪：{emotion_ctx['fusion_emotion']}{_conf_str}")
                    if emotion_ctx.get("live_level"):
                        ctx_lines.append(f"面部表情：{emotion_ctx['live_level']}")
                    if voice_result and voice_result.get("emotion_cn"):
                        ctx_lines.append(f"语调情感：{voice_result['emotion_cn']}（{int(voice_result.get('confidence', 0) * 100)}%）")
                    if text_result and text_result.get("emotion_cn"):
                        ctx_lines.append(f"文本情感：{text_result['emotion_cn']}（{int(text_result.get('confidence', 0) * 100)}%）")
                    if ctx_lines:
                        history.append({
                            "role": "system",
                            "content": "以下是设备自动采集的情绪信号（仅供参考）：\n" + "\n".join(ctx_lines)
                        })

                # 视觉上下文
                if visual_context:
                    history.append({
                        "role": "system",
                        "content": f"摄像头画面内容描述（VLM识别）：{visual_context}"
                    })

                # 对话历史
                history.extend(session.get_chat_history()[-12:])
                log.info("[VC] %s | [3/5] LLM 请求构造完成, history=%d 条", sid, len(history))

                # Dify 智能体入参（对应 chatflow start 节点变量：多模态情绪上下文）
                _emo_ctx = session.emotion_context
                _facial_cn = _emo_ctx.get("live_level", "")
                _facial_conf = float(_emo_ctx.get("live_score", 0) or 0)
                _voice_cn = voice_result.get("emotion_cn", "") if voice_result else ""
                _voice_conf = float(voice_result.get("confidence", 0) or 0) if voice_result else 0
                _text_cn = text_result.get("emotion_cn", "") if text_result else ""
                _text_conf = float(text_result.get("confidence", 0) or 0) if text_result else 0
                dify_inputs = {
                    "user_utterance": asr_text,
                    "fusion_emotion_cn": _emo_ctx.get("fusion_emotion", ""),
                    "fusion_confidence": float(_emo_ctx.get("fusion_confidence", 0) or 0),
                    "facial_emotion_cn": _facial_cn,
                    "facial_confidence": _facial_conf,
                    "voice_emotion_cn": _voice_cn,
                    "voice_confidence": _voice_conf,
                    "text_emotion_cn": _text_cn,
                    "text_confidence": _text_conf,
                    "live_score": _facial_conf,
                    "live_level": _facial_cn,
                    "asr_text": asr_text,
                    "visual_description": visual_context,
                }
                log.info("[VC] %s | Dify inputs: %s", sid, {k: v for k, v in dify_inputs.items() if v})

                # ── 4) LLM 流式输出 + TTS 联动 ─────────────────────
                session.state = realtime_session.STATE_SPEAKING
                await sio.emit("vc_state_change", {"state": "speaking"}, room=sid)
                _llm_provider = "Dify" if use_dify else "DeepSeek"
                log.info("[VC] %s | [4/5] 开始 %s 流式请求 ...", sid, _llm_provider)

                full_response = ""
                sentence_buffer = ""
                token_count = 0
                _first_token_t: float | None = None
                _first_tts_t: float | None = None

                def _call_llm_stream():
                    """在 executor 中调用 LLM 流式 API（Dify / DeepSeek）。"""
                    import requests as _requests
                    if use_dify:
                        _dify_user = clients.get(sid, {}).get("user_id") or sid
                        log.info("[VC] %s | Dify HTTP POST -> %s/chat-messages", sid, _cfg.DIFY_API_BASE)
                        try:
                            return _requests.post(
                                f"{_cfg.DIFY_API_BASE}/chat-messages",
                                headers={
                                    "Authorization": f"Bearer {_api_key}",
                                    "Content-Type": "application/json",
                                },
                                json={
                                    "inputs": dify_inputs,
                                    "query": asr_text,
                                    "response_mode": "streaming",
                                    "user": f"mb-{_dify_user}",
                                    "conversation_id": session.dify_conversation_id,
                                },
                                timeout=_cfg.DIFY_TIMEOUT,
                                stream=True,
                            )
                        except Exception as _e:
                            log.error("[VC] %s | Dify HTTP 请求异常: %s", sid, _e, exc_info=True)
                            raise
                    log.info("[VC] %s | DeepSeek HTTP POST -> %s/chat/completions | model=%s",
                             sid, _cfg.DEEPSEEK_BASE_URL, _cfg.DEEPSEEK_MODEL)
                    try:
                        resp = _requests.post(
                            f"{_cfg.DEEPSEEK_BASE_URL}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {_api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": _cfg.DEEPSEEK_MODEL,
                                "messages": history,
                                "temperature": 0.7,
                                "max_tokens": 500,
                                "stream": True,
                            },
                            timeout=_cfg.DEEPSEEK_TIMEOUT,
                            stream=True,
                        )
                        log.info("[VC] %s | DeepSeek HTTP 响应: status=%s", sid, resp.status_code)
                        return resp
                    except Exception as _e:
                        log.error("[VC] %s | DeepSeek HTTP 请求异常: %s", sid, _e, exc_info=True)
                        raise

                try:
                    resp = await loop.run_in_executor(None, _call_llm_stream)
                except Exception as _e:
                    log.error("[VC] %s | LLM 调用失败 (executor): %s", sid, _e)
                    await sio.emit("vc_error", {
                        "stage": "llm",
                        "message": f"LLM 请求异常: {_e}"
                    }, room=sid)
                    return

                if resp.status_code != 200:
                    err_msg = f"LLM API 返回 {resp.status_code}"
                    try:
                        err_body_txt = resp.text
                        log.error("[VC] %s | LLM 错误响应体: %s", sid, err_body_txt[:500])
                        try:
                            err_body = resp.json()
                            err_msg = err_body.get("error", {}).get("message", err_msg)
                        except Exception:
                            err_msg = f"{err_msg}: {err_body_txt[:200]}"
                    except Exception:
                        pass
                    log.error("[VC] %s | LLM 错误: %s", sid, err_msg)
                    await sio.emit("vc_error", {"stage": "llm", "message": err_msg}, room=sid)
                    return

                # 解析 SSE 流
                import json as _json
                import time as _time
                log.info("[VC] %s | [4/5] 开始解析 SSE 流...", sid)
                line_count = 0
                _llm_resp_t0 = _time.time()
                for line in resp.iter_lines(decode_unicode=True):
                    line_count += 1
                    # 【关键修正】SSE 流处理中即使收到打断，也继续解析（full_response 要完整）
                    #   只在需要触发 TTS 合成时，才根据 llm_cancelled 跳过语音
                    if not line:
                        continue
                    if not line.startswith("data: "):
                        # SSE 中非 data 行（如空行/注释），忽略
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        log.info("[VC] %s | SSE 收到 [DONE], 共 %d 行, %d tokens",
                                 sid, line_count, token_count)
                        break
                    try:
                        chunk_data = _json.loads(data_str)
                        if use_dify:
                            event = chunk_data.get("event", "")
                            if event == "message_end":
                                _cid = chunk_data.get("conversation_id")
                                if _cid:
                                    session.dify_conversation_id = _cid
                                log.info("[VC] %s | Dify message_end | conversation_id=%s",
                                         sid, session.dify_conversation_id)
                                break
                            if event == "error":
                                _err = chunk_data.get("message") or "Dify 智能体错误"
                                log.error("[VC] %s | Dify 错误事件: %s", sid, _err)
                                await sio.emit("vc_error", {"stage": "llm", "message": _err}, room=sid)
                                break
                            if event not in ("message", "agent_message"):
                                continue
                            token = chunk_data.get("answer") or ""
                            if not token:
                                continue
                        else:
                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            token = delta.get("content", "")
                            if not token:
                                continue
                        token_count += 1
                        full_response += token
                        sentence_buffer += token

                        # 推送 token 到前端
                        await sio.emit("vc_llm_token", {"token": token}, room=sid)

                        # 记录首 token 时间（量化延迟用）
                        if _first_token_t is None:
                            _first_token_t = _time.time()
                            log.info("[VC] %s | [4/5] 首 token 到达（距离 HTTP 响应 %.1fs）",
                                     sid, _first_token_t - _llm_resp_t0)

                        # 检查句子边界 → 触发 TTS
                        sentences = _split_sentences(sentence_buffer)
                        force_tts = False
                        force_text = ""
                        if len(sentences) > 1:
                            # 前面的完整句子送 TTS
                            force_tts = True
                            force_text = sentences[0]
                            sentence_buffer = "".join(sentences[1:])
                        elif len(sentence_buffer) >= _MAX_SENTENCE_LEN:
                            # 强制分句：没标点但超过 160 字也切
                            force_tts = True
                            force_text = sentence_buffer[:_MAX_SENTENCE_LEN]
                            sentence_buffer = sentence_buffer[_MAX_SENTENCE_LEN:]

                        if force_tts and force_text.strip() and not session.llm_cancelled:
                            if _first_tts_t is None:
                                _first_tts_t = _time.time()
                                log.info("[VC] %s | [4/5] 首次触发 TTS（首 token → 首个声音输出 %.1fs）",
                                         sid, _first_tts_t - _first_token_t)
                            log.info("[VC] %s | [4/5] %s分句 TTS: %s",
                                     sid, "强制" if len(sentences) <= 1 else "标点", force_text[:40])

                            await sio.emit("vc_tts_start", {"text": force_text}, room=sid)
                            try:
                                tts_chunk_idx = 0
                                tts_was_cancelled_during = False
                                async for audio_chunk in _tts.synthesize(
                                    force_text, voice=_cfg.TTS_VOICE, rate=_cfg.TTS_RATE
                                ):
                                    # 关键修改：TTS 合成过程中即使被打断，也继续把当前句的音频发完
                                    # 这样用户能完整听到这一句话，而不是只播放一半
                                    if session.llm_cancelled:
                                        tts_was_cancelled_during = True
                                    tts_chunk_idx += 1
                                    audio_b64 = _b64.b64encode(audio_chunk).decode("ascii")
                                    await sio.emit("vc_tts_chunk", {
                                        "data": audio_b64,
                                        "format": "mp3",
                                    }, room=sid)
                                if tts_was_cancelled_during:
                                    log.info("[VC] %s | [4/5] TTS 句完成（期间被打断，已完整合成 %d 个分片，后续只收文字不再TTS）", sid, tts_chunk_idx)
                                else:
                                    log.info("[VC] %s | [4/5] TTS 句完成: %d 个分片", sid, tts_chunk_idx)
                                await sio.emit("vc_tts_done", {"text": force_text}, room=sid)
                                # 【关键修正】即使被打断也不退出 SSE 循环！
                                #   vc_interrupt 的唯一语义 = 停止后续 TTS 语音播放
                                #   LLM 文字生成必须完整解析到底，保证用户能看到完整文字回复
                                #   后续句子会因为 session.llm_cancelled=True 而自动跳过 TTS
                                #   绝不能 break，否则 full_response 就残缺了！
                            except Exception as e:
                                log.warning("[VC] %s | TTS 分句失败: %s", sid, e, exc_info=True)
                                await sio.emit("vc_error", {
                                    "stage": "tts", "message": str(e)
                                }, room=sid)

                    except _json.JSONDecodeError:
                        continue

                log.info("[VC] %s | [4/5] SSE 解析完成, full_response 长度=%d, token_count=%d",
                         sid, len(full_response), token_count)

                # 处理缓冲区中剩余的文本
                # 【关键修正】尾句是否合成TTS取决于 llm_cancelled，但无论如何文字都已在 full_response 中
                if sentence_buffer.strip():
                    if not session.llm_cancelled:
                        tts_text = sentence_buffer.strip()
                        log.info("[VC] %s | [4/5] 尾句 TTS: %s", sid, tts_text[:40])
                        await sio.emit("vc_tts_start", {"text": tts_text}, room=sid)
                        try:
                            async for audio_chunk in _tts.synthesize(
                                tts_text, voice=_cfg.TTS_VOICE, rate=_cfg.TTS_RATE
                            ):
                                audio_b64 = _b64.b64encode(audio_chunk).decode("ascii")
                                await sio.emit("vc_tts_chunk", {
                                    "data": audio_b64,
                                    "format": "mp3",
                                }, room=sid)
                            await sio.emit("vc_tts_done", {"text": tts_text}, room=sid)
                        except Exception as e:
                            log.warning("[VC] %s | TTS(尾句)失败: %s", sid, e, exc_info=True)
                    else:
                        # 被打断 → 尾句不合成 TTS，但文字完整保留在 full_response 中
                        log.info("[VC] %s | [4/5] 尾句 %d 字（已被打断，跳过TTS，仅保留文字）",
                                 sid, len(sentence_buffer.strip()))

                # ── 5) LLM 完成收尾 ───────────────────────────────
                _was_interrupted = session.llm_cancelled
                log.info("[VC] %s | [5/5] 收尾 | 被打断=%s | full_response=%d字 | tokens=%d | 内容=[%s]",
                         sid, _was_interrupted, len(full_response), token_count,
                         full_response[:80] if full_response else "(空)")

                if not full_response.strip():
                    # 只有"完全没生成内容"才报错（真·空回复才是配置问题）
                    if not _was_interrupted:
                        log.warning("[VC] %s | LLM 返回空内容（非打断导致）！", sid)
                        await sio.emit("vc_error", {
                            "stage": "llm_empty",
                            "message": "AI 回复为空，请检查 API Key 是否有效或额度是否充足"
                        }, room=sid)
                    else:
                        log.info("[VC] %s | LLM 被打断时还没生成内容（正常）", sid)
                else:
                    # 不管有没有被打断，只要生成了内容，就 emit 给前端显示
                    await sio.emit("vc_llm_done", {"full_response": full_response}, room=sid)
                    session.add_chat_message("assistant", full_response)
                    if _was_interrupted:
                        log.info("[VC] %s | <<< 管线结束（被打断，已保存 %d 字内容）", sid, len(full_response))
                    else:
                        log.info("[VC] %s | <<< 管线全部完成（自然结束）, 回复长度=%d, tokens=%d",
                                 sid, len(full_response), token_count)

            finally:
                # 清理上传的音频文件（仅限 vc_uploads 目录下的）
                try:
                    if _VC_UPLOAD_DIR in audio_path and os.path.isfile(audio_path):
                        os.remove(audio_path)
                        log.info("[VC] %s | 已清理上传文件: %s", sid, audio_path)
                except Exception as _e:
                    log.warning("[VC] %s | 清理音频文件失败: %s", sid, _e)

        except Exception as e:
            log.error("[VC] %s | 管线异常: %s", sid, e, exc_info=True)
            await sio.emit("vc_error", {
                "stage": "pipeline",
                "message": f"处理失败: {e}",
            }, room=sid)
        finally:
            # 如果会话已结束（用户点了结束通话），不再发 listening 覆盖 idle
            if session.state == realtime_session.STATE_IDLE:
                log.info("[VC] %s | 管线结束，但会话已关闭，跳过状态重置", sid)
                return
            # 回到监听状态
            session.state = realtime_session.STATE_LISTENING
            await sio.emit("vc_state_change", {"state": "listening"}, room=sid)
            session.reset_interrupt()
            log.info("[VC] %s | 状态重置 -> listening", sid)

    # ─── vc_start: 开始视频通话会话 ─────────────────────────────
    @sio.on("vc_start")
    async def handle_vc_start(sid, data=None):
        from app.services.ai_lab import realtime_session
        session = realtime_session.get_session(sid)
        session.state = realtime_session.STATE_LISTENING
        log.info("[VC] %s | 视频通话开始", sid)
        await sio.emit("vc_state_change", {"state": "listening"}, room=sid)

    # ─── vc_stop: 结束视频通话会话 ─────────────────────────────
    @sio.on("vc_stop")
    async def handle_vc_stop(sid, data=None):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            return
        session = realtime_session.get_session(sid)
        # 彻底取消所有进行中的任务
        session.llm_cancelled = True
        session.interrupted = True
        session.state = realtime_session.STATE_IDLE
        # 清理累积的音频和情感数据
        session.audio_chunks.clear()
        session.emotion_result = None
        log.info("[VC] %s | 视频通话结束（会话已清理）", sid)
        # 通知前端状态变为 idle
        await sio.emit("vc_state_change", {"state": "idle"}, room=sid)
        # 延迟 2 秒后彻底移除会话（确保所有进行中的事件都已处理完毕）
        import asyncio
        await asyncio.sleep(2)
        if realtime_session.has_session(sid):
            realtime_session.remove_session(sid)
            log.info("[VC] %s | 会话已彻底移除", sid)

    # ─── vc_audio_chunk: 接收音频分片 ──────────────────────────
    @sio.on("vc_audio_chunk")
    async def handle_vc_audio_chunk(sid, data):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            return
        session = realtime_session.get_session(sid)
        if isinstance(data, dict):
            chunk_b64 = data.get("data", "")
        else:
            chunk_b64 = str(data) if data else ""
        if chunk_b64:
            session.add_audio_chunk(chunk_b64)

    # ─── vc_audio_end: 用户说完话，触发 ASR → LLM → TTS ────────
    # data 格式（新方案）: { file_id: "xxx", file_size: 12345 }
    # 音频文件已通过 HTTP POST /api/vc_audio_upload 保存到磁盘
    _VC_UPLOAD_DIR = _os.path.join(_tmp.gettempdir(), "vc_uploads")

    @sio.on("vc_audio_end")
    async def handle_vc_audio_end(sid, data=None):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            return

        # 如果会话已关闭（用户点了结束通话），不再处理
        session = realtime_session.get_session(sid)
        if session.state == realtime_session.STATE_IDLE:
            log.info("[VC] %s | 会话已关闭，忽略音频处理", sid)
            return

        file_id = ""
        file_size = 0
        if isinstance(data, dict):
            file_id = data.get("file_id", "") or ""
            file_size = data.get("file_size", 0) or 0

        if not file_id:
            log.warning("[VC] %s | 缺少 file_id，忽略", sid)
            return

        # 在上传目录中查找对应文件
        audio_path = ""
        for ext in (".webm", ".webma", ".ogg", ".mp3", ".wav", ".opus"):
            candidate = _os.path.join(_VC_UPLOAD_DIR, f"{file_id}{ext}")
            if _os.path.isfile(candidate):
                audio_path = candidate
                break

        if not audio_path:
            log.error("[VC] %s | 找不到文件 file_id=%s", sid, file_id)
            await sio.emit("vc_error", {
                "stage": "upload", "message": f"找不到音频文件(file_id={file_id})"
            }, room=sid)
            return

        actual_size = _os.path.getsize(audio_path)
        log.info("[VC] %s | 收到音频结束, file=%s, size=%dB (上报=%dB)",
                 sid, _os.path.basename(audio_path), actual_size, file_size)

        if actual_size < 1000:
            log.warning("[VC] %s | 音频过小(%dB)，忽略", sid, actual_size)
            return

        # 异步执行管线，不阻塞 socket 事件循环
        import asyncio
        asyncio.ensure_future(_run_video_call_pipeline_from_file(sid, audio_path))

    # ─── vc_interrupt: 用户打断 ────────────────────────────────
    @sio.on("vc_interrupt")
    async def handle_vc_interrupt(sid, data=None):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            return
        session = realtime_session.get_session(sid)
        # 【关键修正】vc_interrupt 的唯一语义：停止后续 TTS 语音合成
        #   1. 只设置 llm_cancelled=True，让管线内的 TTS 判断跳过
        #   2. 绝对不设置 state=listening！因为 ASR/情感/LLM 还在执行！
        #   3. 真正的 state=listening 在管线 finally 里统一设置
        #   否则前端会以为处理结束了，清空 partialAssistantText 并重启录音，
        #   但后端 LLM 还在发 token，导致文字显示乱序或丢失
        session.llm_cancelled = True
        session.interrupted = True
        log.info("[VC] %s | 用户打断（仅停止TTS，LLM文字继续生成）", sid)
        # 通知前端：TTS 已被打断（让前端停止播放并准备新一轮录音）
        # 但明确标注是"打断"，不是"处理结束"
        await sio.emit("vc_interrupted", {}, room=sid)

    # ─── vc_update_frame: 更新视频帧（供 VLM 使用，与面部识别独立）──
    @sio.on("vc_update_frame")
    async def handle_vc_update_frame(sid, data):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            return
        if isinstance(data, dict) and data.get("imgBase64"):
            session = realtime_session.get_session(sid)
            session.update_frame(data["imgBase64"])

    # ─── vc_update_emotion: 更新情绪上下文（从面部识别结果同步）──
    @sio.on("vc_update_emotion")
    async def handle_vc_update_emotion(sid, data):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            return
        if isinstance(data, dict):
            session = realtime_session.get_session(sid)
            session.emotion_context.update(data)

    # ─── vc_clear_history: 清空对话历史 ────────────────────────
    @sio.on("vc_clear_history")
    async def handle_vc_clear_history(sid, data=None):
        from app.services.ai_lab import realtime_session
        if realtime_session.has_session(sid):
            session = realtime_session.get_session(sid)
            session.chat_history.clear()
            session.last_visual_description = ""
            log.info("[VC] %s | 对话历史已清空", sid)

    log.info("[INIT] SocketIO 情绪识别路由注册完成 | 事件: connect, disconnect, upload_frame, upload_audio, vc_start, vc_stop, vc_audio_chunk, vc_audio_end, vc_interrupt, vc_update_frame, vc_update_emotion, vc_clear_history")
