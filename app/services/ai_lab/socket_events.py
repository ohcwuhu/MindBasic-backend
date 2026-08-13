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

    # ─── 连接事件：初始化客户端独立状态 ────────────────────────────
    @sio.on("connect")
    async def handle_connect(sid, environ):
        clients[sid] = {
            "last_frame_time": 0.0,    # 上次成功推理的时间戳（节流用）
            "connect_at": time.time(), # 连接建立时间（日志用）
        }
        # 初始化面部时序缓冲（供 HTTP 层 /api/analyze_audio 融合时查询）
        facial_buffer.init_client(sid)
        log.info(f"[CONNECT] {sid} | 当前在线客户端数: {len(clients)}")

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

    log.info("[INIT] SocketIO 情绪识别路由注册完成 | 事件: connect, disconnect, upload_frame, upload_audio")
