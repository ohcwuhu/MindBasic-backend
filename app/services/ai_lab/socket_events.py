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
import re
import time
import base64
import asyncio
from datetime import datetime

# cv2 / numpy / DeepFace 在函数内懒加载，避免应用启动即加载 TensorFlow/torch

# ─── 面部时序缓冲（供 HTTP 层 /api/analyze_audio 融合时查询）─────────
from app.services.ai_lab import facial_buffer

# ─── 后台任务登记（保持强引用，避免留痕任务被 GC 回收）───────────────
from app.core.tasks import spawn_task

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

# ─── 上传音频文件 ID 的格式约束 ───────────────────────────────────────
# 与 /api/vc_audio_upload 生成的 uuid4().hex 一致；严格校验是为了杜绝
# 客户端用 "../" 之类的 file_id 越出上传目录（拼路径前先挡掉）。
UPLOAD_FILE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

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

# ─── 危机场景：追加给 LLM 的处置指令 ────────────────────────────────
# 仅在规则引擎判定 MEDIUM/HIGH 时注入，避免普通对话被安全话术占据
_CRISIS_SYSTEM_DIRECTIVE = """【本轮安全处置要求（优先级高于其他风格要求）】
系统检测到用户本轮表达可能存在自伤/自杀风险信号。请按以下顺序回应：
1. 先表达关心与接纳，不评判、不追问方式细节，不渲染悲剧感；
2. 明确建议用户拨打全国心理援助热线 12356（24 小时），或前往就近医院急诊，
   并鼓励其联系信任的人陪伴；
3. 不进行任何心理/精神疾病诊断，不提供医疗建议，不讨论自伤方式；
4. 回复保持简短、口语化，适合语音播放。"""

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


# ─── Dify 自判风险等级：字段名兼容多种工作流写法 ──────────────────────
_DIFY_RISK_KEYS = ("risk_level", "riskLevel", "dify_risk_level", "risk")


def _extract_dify_risk(payload: object) -> str | None:
    """从一路 Dify SSE 事件里取出工作流自判的风险等级（取不到返回 ``None``）。

    不同工作流的暴露方式不同，按命中概率依次尝试：

    1. ``workflow_finished`` 的 ``data.outputs``（结束节点输出变量）；
    2. ``message_end`` 的 ``metadata``；
    3. 事件顶层 / ``data`` 里的同名字段。

    只接受字符串或数字，避免把结构化对象当成等级写进留痕。
    """
    if not isinstance(payload, dict):
        return None

    sources: list[dict] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        sources.append(data)
        outputs = data.get("outputs")
        if isinstance(outputs, dict):
            sources.append(outputs)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        sources.append(metadata)

    for source in sources:
        for key in _DIFY_RISK_KEYS:
            value = source.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
    return None


async def _backfill_dify_risk(snapshot_task, session_id: str, level: str) -> None:
    """等本轮留痕落库后，把 Dify 自判风险等级补写到该行。

    顺序敏感：留痕是后台任务写的，必须等它提交完成再 UPDATE，
    否则会补写到上一轮的行上（同一会话同一时刻只跑一轮管线）。
    """

    from app.services.analysis_record_service import update_dify_risk_level

    if snapshot_task is not None:
        try:
            await asyncio.shield(snapshot_task)
        except Exception:  # noqa: BLE001 - 留痕失败不影响主流程
            return
    await update_dify_risk_level(session_id, level)


async def _backfill_fallback_risk(
    loop,
    snapshot_task,
    session_id: str,
    user_text: str,
    *,
    recent_user_lines: list[str] | None = None,
    emotion_line: str = "",
) -> None:
    """兜底轮次的第二意见风险自判：判定后补写留痕。

    放在主流程之外的三个理由：

    1. 判定要发一次额外的 LLM 请求，绝不能拖慢用户这一轮的首 token；
    2. 与 ``_backfill_dify_risk`` 同构——都要等留痕落库后再 UPDATE，
       否则会补写到上一轮的行上；
    3. 失败只是没有第二意见，不影响任何用户可见行为（内部已吞掉异常）。
    """
    from app.services.analysis_record_service import update_fallback_risk_level
    from app.services.ai_lab import risk_judge

    if not risk_judge.is_enabled():
        return
    level, reason = await loop.run_in_executor(
        None,
        lambda: risk_judge.judge_risk(
            user_text,
            recent_user_lines=recent_user_lines,
            emotion_line=emotion_line,
        ),
    )
    if not level:
        return
    log.info("[VC] %s | 兜底自判风险等级: %s（%s），补写本轮留痕",
             session_id, level, reason or "无依据说明")
    if snapshot_task is not None:
        try:
            await asyncio.shield(snapshot_task)
        except Exception:  # noqa: BLE001 - 留痕失败不影响主流程
            return
    await update_fallback_risk_level(session_id, level)


async def _warm_dify_input_types() -> None:
    """后台预热 Dify 入参声明缓存（``GET /parameters``）。

    这是管线里唯一需要联网拿的配置，缓存 300s 过期。通话开始到第一轮回复之间
    通常有几秒（用户在说话 + ASR 处理），足够这次往返跑完，于是第一轮不必再等它
    ——实测云端往返 1.7~6.8s，直接加在首字延迟上。

    ``fetch_input_types`` 自己吞掉异常并回退到旧缓存，所以这里不需要再兜一层；
    真失败也只是没预热到，不影响任何用户可见行为。
    """
    from app.services.ai_lab import dify_service

    if not dify_service.is_enabled():
        return
    await asyncio.get_running_loop().run_in_executor(
        None, dify_service.fetch_input_types
    )


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
        raise ValueError(f"Failed to decode base64 frame: {e}") from e


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


# ============================================================
#  SSE 流消费：Token 实时推流 + 按句联动 TTS
#  提到模块级是为了可测：此前它藏在 register_socket_events 闭包里，
#  闭包里的 _cfg / _b64 缺 import 也没人能测出来（分句 TTS 一直被跳过）。
# ============================================================

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

async def _consume_llm_stream(sio, log, resp, *, is_dify: bool, sid: str, session) -> dict:
    """解析一路 LLM 的 SSE 响应：Token 实时推前端、按句联动 TTS。

    返回 ``{full_response, sentence_buffer, token_count, first_token_t,
    first_tts_t, resp_t0, error}``。``full_response`` 为空或 ``error`` 非空，
    都表示这一路没跑出可用内容，调用方据此决定是否降级到下一个供应商。
    """
    import json as _json
    import base64 as _b64
    import time as _jtime

    from app.services.ai_lab import config as _cfg
    from app.services.ai_lab import tts_service as _tts

    full_response = ""
    sentence_buffer = ""
    token_count = 0
    # 推理模式残留计数：正常关闭推理时为 0。若某个模型仍输出 reasoning_content，
    # 这些内容不朗读、不展示，但必须留痕——否则"回复变慢/变空"会被误判成网络问题。
    reasoning_chars = 0
    first_token_t: float | None = None
    first_tts_t: float | None = None
    error: str | None = None
    dify_risk_level: str | None = None
    dify_saw_message_end = False
    dify_post_end_events = 0
    line_count = 0
    resp_t0 = _jtime.time()

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
            if is_dify:
                # 工作流自判风险等级（可能出现在 workflow_finished 的 outputs
                # 或 message_end 的 metadata 里），取到即留痕，供两侧一致性统计
                _found_risk = _extract_dify_risk(chunk_data)
                if _found_risk:
                    dify_risk_level = _found_risk
                event = chunk_data.get("event", "")
                if event == "message_end":
                    _cid = chunk_data.get("conversation_id")
                    if _cid:
                        session.dify_conversation_id = _cid
                    log.info("[VC] %s | Dify message_end | conversation_id=%s",
                             sid, session.dify_conversation_id)
                    # message_end 之后通常还有 workflow_finished（携带结束节点
                    # 输出变量，例如工作流自判的 risk_level），继续读到流结束，
                    # 否则拿不到工作流的输出，两侧一致性永远为空。
                    # 最多再等 20 个事件，避免异常服务端不回结束事件时卡住。
                    dify_saw_message_end = True
                    continue
                if dify_saw_message_end:
                    dify_post_end_events += 1
                    if dify_post_end_events > 20:
                        log.warning(
                            "[VC] %s | message_end 后仍未收到 workflow_finished，提前结束解析", sid,
                        )
                        break
                if event == "workflow_finished":
                    log.info("[VC] %s | Dify workflow_finished | risk=%s",
                             sid, dify_risk_level or "(未暴露)")
                    break
                if event == "error":
                    error = chunk_data.get("message") or "Dify 智能体错误"
                    log.error("[VC] %s | Dify 错误事件: %s", sid, error)
                    break
                if event not in ("message", "agent_message"):
                    continue
                token = chunk_data.get("answer") or ""
                if not token:
                    continue
            else:
                delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                # 推理模型的思考过程：只计数，绝不进入正文（正文会被 TTS 朗读）
                _reasoning = delta.get("reasoning_content")
                if _reasoning:
                    reasoning_chars += len(str(_reasoning))
                if not token:
                    continue
            token_count += 1
            full_response += token
            sentence_buffer += token

            # 推送 token 到前端
            await sio.emit("vc_llm_token", {"token": token}, room=sid)

            # 记录首 token 时间（量化延迟用）
            if first_token_t is None:
                first_token_t = _jtime.time()
                log.info("[VC] %s | [4/5] 首 token 到达（距离 HTTP 响应 %.1fs）",
                         sid, first_token_t - resp_t0)

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
                if first_tts_t is None:
                    first_tts_t = _jtime.time()
                    log.info("[VC] %s | [4/5] 首次触发 TTS（首 token → 首个声音输出 %.1fs）",
                             sid, first_tts_t - first_token_t)
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
    if reasoning_chars:
        log.warning(
            "[VC] %s | 模型输出了 %d 字推理内容（已丢弃，不入正文/不朗读）。"
            "说明推理模式未关闭：首 token 会变慢，且正文可能被推理 token 挤空——"
            "检查 DEEPSEEK_DISABLE_REASONING 与 max_tokens。",
            sid, reasoning_chars,
        )
    return {
        "full_response": full_response,
        "sentence_buffer": sentence_buffer,
        "token_count": token_count,
        "first_token_t": first_token_t,
        "first_tts_t": first_tts_t,
        "resp_t0": resp_t0,
        "error": error,
        "dify_risk_level": dify_risk_level,
    }


def register_socket_events(sio, log):
    """
    注册所有 SocketIO 事件处理器到主实例。
    在 main.py 中调用：register_socket_events(sio, log)
    """
    import os as _os
    import tempfile as _tmp

    from app.services.ai_lab import realtime_session as _rt

    # ─── 空闲看门狗收尾：长时间无语音时由定时任务触发（见 realtime_session）───
    async def _handle_idle_timeout(sid: str, idle_seconds: int) -> None:
        """把长时间无语音的通话收尾：通知前端、置空闲、归档会话。

        前端自己也会在无语音到点后结束通话；这里是服务端兜底，
        覆盖"标签页被挂起 / 前端定时器被浏览器节流"的情况。
        """
        from app.services import ai_conversation_service as _conv

        log.info("[VC] %s | 无语音 %ds，服务端自动结束通话", sid, idle_seconds)
        session = _rt.get_session(sid) if _rt.has_session(sid) else None
        conv_id = session.conversation_id if session else None

        if session is not None:
            session.state = _rt.STATE_IDLE
            session.llm_cancelled = True
            session.audio_chunks.clear()
            session.conversation_id = None
        clients.get(sid, {}).pop("ai_conv_id", None)

        await sio.emit("vc_idle_timeout", {
            "reason": "no_speech",
            "idleSeconds": idle_seconds,
            "message": "长时间没有听到你的声音，通话已自动结束",
        }, room=sid)
        await sio.emit("vc_state_change", {"state": "idle"}, room=sid)

        if conv_id:
            await _conv.end_session_safely(conv_id, status="ABANDONED")

    _rt.set_idle_timeout_handler(_handle_idle_timeout)

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
        # 断线时收尾 AI 对话会话：客户端异常断开时不会触发 vc_stop
        from app.services.ai_conversation_service import end_session_safely

        _session = None
        try:
            from app.services.ai_lab import realtime_session as _rt

            if _rt.has_session(sid):
                _session = _rt.get_session(sid)
        except Exception:  # noqa: BLE001 - 断线清理不得因读取会话失败而中断
            _session = None
        _conv_id = getattr(_session, "conversation_id", None) if _session else None
        if _conv_id:
            await end_session_safely(_conv_id, status="ABANDONED")
            log.info("[VC] %s | 断线，AI 会话 %s 标记为异常中断", sid, _conv_id)
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

        # 1.5) 授权兜底：视频通话会话已撤回摄像头授权时，服务端拒绝处理画面帧
        #      （前端不再上传是默认行为，这里是"撤回即刻生效"的服务端保证）
        from app.services.ai_lab import realtime_session as _vc_sessions
        if _vc_sessions.has_session(sid) and not _vc_sessions.get_session(sid).consent_camera:
            log.debug("[SKIP] %s | 已撤回摄像头授权，丢弃画面帧", sid)
            return

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
        #    推理是 CPU 密集的同步调用，必须丢到线程池：
        #    直接在事件循环里跑会把同一进程的 HTTP 请求与其他 socket 连接一起卡住。
        try:
            analysis = await asyncio.get_running_loop().run_in_executor(
                None, analyze_emotion, frame
            )
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


    async def _run_video_call_pipeline_from_file(sid: str, audio_path: str):
        """视频通话核心管线（新入口：直接接收磁盘文件路径）。"""
        import base64 as _b64
        import time as _time

        from app.services.ai_lab import realtime_session
        from app.services.ai_lab import sensevoice_service as _sv
        from app.services.ai_lab import tts_service as _tts
        from app.services.ai_lab import vlm_service as _vlm
        from app.services.ai_lab import config as _cfg
        from app.services.ai_lab import emotion2vec_service as _ev
        from app.services.ai_lab import text_emotion_service as _te
        from app.services.ai_lab import opensmile_service as _oss
        from app.services.ai_lab import fusion_service as _fs
        from app.services import crisis_service as _crisis
        from app.services.analysis_record_service import (
            AnalysisSnapshot as _AnalysisSnapshot,
            save_snapshot as _save_analysis_snapshot,
        )
        from app.services import ai_conversation_service as _conversation
        from app.services.coach_stage_service import decide_stage as _decide_stage
        from app.services.ai_lab import dify_service as _dify
        from functools import partial as _partial

        from app.services.ai_lab import kb_cards as _kb

        from app.services.ai_lab import fallback_prompt as _fallback

        session = realtime_session.get_session(sid)
        session.state = realtime_session.STATE_THINKING
        await sio.emit("vc_state_change", {"state": "thinking"}, room=sid)

        # 一轮对话的计时起点（端到端耗时以"收到音频"为基准）
        _turn_t0 = _time.time()

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
                loop = asyncio.get_event_loop()

                # ── 情感分析函数：提前定义，好在 ASR 期间就把语调分析并行启动 ──
                # 语调情感只读音频文件（与 ASR 读的是同一个 audio_path），没有任何
                # 数据依赖；只有文本情感需要 ASR 文本。放在这里定义，纯粹是为了让
                # 下面能在 await ASR 之前把语调分析丢进线程池。
                def _run_voice_emotion():
                    """语调情感分析，emotion2vec 优先，失败降级 opensmile。"""
                    try:
                        result = _ev.analyze(audio_path)
                        result["_fallback_used"] = False
                        return result
                    except Exception as e1:
                        # 偶发抖动（如 dtype/并发导致的单次失败）重试一次，
                        # 避免直接掉到精度差得多的 opensmile 兜底
                        log.warning("[VC] %s | emotion2vec 失败，重试一次: %s", sid, e1)
                        try:
                            result = _ev.analyze(audio_path)
                            result["_fallback_used"] = False
                            result["_retried"] = True
                            return result
                        except Exception as e1b:
                            log.warning("[VC] %s | emotion2vec 重试仍失败, 降级 opensmile: %s", sid, e1b)
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
                    """文本情感分析（依赖 ASR 文本，故读取闭包里的 asr_text）。"""
                    try:
                        return _te.analyze(asr_text)
                    except Exception as e:
                        log.warning("[VC] %s | text_emotion 失败: %s", sid, e)
                        return None

                # ── 1) SenseVoice ASR ──────────────────────────────
                log.info("[VC] %s | [1/5] 开始 ASR 识别...", sid)
                _asr_t0 = _time.time()

                # 【延迟优化】语调情感与 ASR 并行启动。两者都是重模型（CPU 上会争抢
                # 核心，所以收益不是"完全省掉"，但重叠仍能实打实缩短关键路径）。
                # 未授权多模态时不做语调分析，与下方分支的判断保持一致。
                _voice_future = (
                    loop.run_in_executor(None, _run_voice_emotion)
                    if session.consent_multimodal else None
                )

                asr_result = await loop.run_in_executor(None, _sv.transcribe, audio_path)
                _asr_seconds = round(_time.time() - _asr_t0, 3)
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

                # 知识库索引预热：进程首次调用要构建索引（约 0.8s），放在这里与
                # 多模态分析重叠，不占用关键路径。真正的检索在下面拿到阶段与风险
                # 等级之后再执行——卡片知识库只用本地 jieba + BM25，毫秒级。
                _kb_future = asyncio.ensure_future(
                    loop.run_in_executor(None, _kb.load)
                )

                # VLM 视觉理解提前启动：它依赖 ASR 文本，但不依赖情感分析结果。
                # 原先它串行排在多模态之后，一触发就把整段耗时加到关键路径上；
                # 放在这里与多模态分析并行，结果在下面第 2 步再取。
                _vlm_future = None
                if _is_visual_query(asr_text):
                    _vlm_frame = session.get_valid_frame()
                    if _vlm_frame is not None and _vlm.is_available():
                        log.info("[VC] %s | [2/5] VLM 与本轮多模态分析并行启动", sid)
                        _vlm_future = loop.run_in_executor(
                            None, _vlm.analyze_frame, _vlm_frame, asr_text,
                            session.get_chat_history(),
                        )

                # 【关键修正】ASR 完成后绝不因为"打断"而丢弃
                #   vc_interrupt 的唯一语义 = 停止后续 TTS 语音播放
                #   ASR / 情感分析 / LLM 文本生成 必须完整执行，保证用户看到文字回复
                #   TTS 合成时才会尊重 llm_cancelled 跳过语音合成

                # ── 1.5) 多模态情感分析（语调+文本+面部融合）─────────
                log.info("[VC] %s | [1.5/5] 开始多模态情感分析...", sid)
                import time as _time_mm
                _mm_t0 = _time_mm.time()

                # 说明：两个分析函数已在 ASR 之前定义（见上方），以便语调情感能与
                # ASR 并行。这里只负责取结果。
                if not session.consent_multimodal:
                    # 用户未授权多模态分析：跳过语调分析，只保留文本情感。
                    # 授权范围见 vc_start 写入的 consent 快照与 consent_records 表。
                    log.info("[VC] %s | 用户未授权多模态分析，本轮跳过语调情感", sid)
                    voice_result = None
                    text_result = await loop.run_in_executor(None, _run_text_emotion)
                else:
                    # 语调情感在 ASR 期间就已经在跑了（_voice_future），这里 await 通常
                    # 立刻返回；文本情感依赖 ASR 文本，只能从这一刻开始，与它并行等待。
                    voice_result, text_result = await asyncio.gather(
                        _voice_future,
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
                        include_facial=session.consent_multimodal,
                    )
                except Exception as e:
                    log.warning("[VC] %s | fusion_service 失败: %s", sid, e, exc_info=True)

                _mm_elapsed = _time_mm.time() - _mm_t0

                # 提取融合结果，写入 emotion_context 供 LLM 使用
                if fusion_result and fusion_result.get("fusion"):
                    _f = fusion_result["fusion"]
                    _facial = fusion_result.get("facial_emotion", {})
                    _conflict = _f.get("conflict") or {}
                    session.emotion_context.update({
                        "fusion_emotion": _f.get("final_emotion_cn", ""),
                        "fusion_emotion_en": _f.get("final_emotion", ""),
                        "fusion_confidence": _f.get("overall_confidence", 0),
                        "live_score": _facial.get("confidence", 0),
                        "live_level": _facial.get("dominant_emotion_cn", ""),
                        "conflict_level": _conflict.get("level", ""),
                        "needs_clarification": bool(_conflict.get("needs_clarification")),
                        "conflict_reason": _conflict.get("reason", ""),
                    })
                    log.info("[VC] %s | [1.5/5] 多模态融合完成 (%.1fs) | 融合情绪=%s(%.0f%%) | 面部=%s | 语调=%s | 文本=%s | 冲突=%s",
                             sid, _mm_elapsed,
                             _f.get("final_emotion_cn", "?"),
                             _f.get("overall_confidence", 0) * 100,
                             _facial.get("dominant_emotion_cn", "无数据"),
                             voice_result.get("emotion_cn", "无") if voice_result else "无",
                             text_result.get("emotion_cn", "无") if text_result else "无",
                             _conflict.get("level", "?"))
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

                # ── 1.8) 危机风险分级（文本 + 多模态信号）────────────
                _user_id = clients.get(sid, {}).get("user_id")
                _facial_summary = (fusion_result or {}).get("facial_emotion") or {}
                _risk_signals = {
                    "voice_emotion": voice_result.get("emotion") if voice_result else None,
                    "voice_confidence": voice_result.get("confidence", 0.0) if voice_result else 0.0,
                    "facial_emotion": _facial_summary.get("dominant_emotion"),
                    "facial_confidence": _facial_summary.get("confidence", 0.0),
                    "facial_frames": _facial_summary.get("frame_count", 0),
                }
                _risk = _crisis.assess(asr_text, _risk_signals)
                session.emotion_context["risk_level"] = _risk.level
                session.emotion_context["risk_score"] = _risk.risk_score
                log.info(
                    "[VC] %s | [1.8/5] 风险分级: %s（评分 %s）| %s",
                    sid, _risk.level, _risk.risk_score,
                    "；".join(_risk.reasons[:2]) or "无命中",
                )

                if _risk.flagged:
                    await sio.emit("vc_crisis_alert", {
                        "level": _risk.level,
                        "levelLabel": _risk.level_label_cn,
                        "riskScore": _risk.risk_score,
                        "reasons": list(_risk.reasons),
                        "hotline": "12356",
                    }, room=sid)
                    if _user_id:
                        # 建档写库不阻塞对话：失败只记日志
                        spawn_task(
                            _crisis.flag_crisis_safely(
                                int(_user_id),
                                _crisis.SOURCE_VIDEO_CALL,
                                asr_text,
                                assessment=_risk,
                            ),
                            name=f"crisis-flag:{sid}",
                        )

                # ── 1.9) 五阶段状态判定（平台侧确定性引擎）─────────────
                #   结果落库并作为入参回传给 Dify，保证"阶段口径"只有一个来源。
                _stage_decision = _decide_stage(
                    session.get_chat_history(),
                    asr_text,
                    turn_index=session.turn_index + 1,
                )
                session.stage_context = _stage_decision.to_dict()
                session.emotion_context["coach_stage"] = _stage_decision.stage
                log.info(
                    "[VC] %s | [1.9/5] 阶段判定: %s (goal=%s action=%s summarize=%s) | %s",
                    sid, _stage_decision.stage_label_cn,
                    _stage_decision.goal_clear, _stage_decision.action_ready,
                    _stage_decision.should_summarize,
                    "；".join(_stage_decision.evidence) or "无线索",
                )

                # 分析留痕：让识别结果成为可统计的数据资产（失败不影响对话）
                # 保持任务引用：管线结束后要用它把 Dify 自判风险等级补写到本行
                _snapshot_task = spawn_task(_save_analysis_snapshot(
                    _AnalysisSnapshot.from_video_call(
                        user_id=int(_user_id) if _user_id else None,
                        session_id=sid,
                        asr_text=asr_text,
                        asr_emotion=emo,
                        text_emotion=text_result,
                        voice_emotion=voice_result,
                        fusion=(fusion_result or {}).get("fusion"),
                        facial_emotion=_facial_summary,
                        risk=_risk.to_dict(),
                        stage=_stage_decision.to_dict(),
                        # 此处先留空：Dify 的自判等级要等 LLM 结束事件
                        # （workflow_finished / message_end）才拿得到，
                        # 由 _backfill_dify_risk 在本轮结束后补写该行。
                        # 工作流确实没暴露时保持 NULL，一致性统计会跳过该行。
                        dify_risk_level=None,
                        conversation_id=session.conversation_id,
                        timings={
                            "asr_seconds": _asr_seconds,
                            "multimodal_seconds": round(_mm_elapsed, 3),
                        },
                        status="ok" if fusion_result else "partial_success",
                    )
                ), name=f"analysis-snapshot:{sid}")

                # ── 1.95) 用户表达落库（先落库再生成，被打断也不丢）──────
                #   尽力而为：写库失败只记日志，不影响用户体验。
                spawn_task(_conversation.record_message_safely(
                    session.conversation_id,
                    role="USER",
                    content=asr_text,
                    turn_index=session.turn_index + 1,
                    emotion={
                        "fusion_emotion_cn": session.emotion_context.get("fusion_emotion", ""),
                        "fusion_emotion_en": session.emotion_context.get("fusion_emotion_en", ""),
                        "fusion_confidence": session.emotion_context.get("fusion_confidence", 0),
                        "live_level": session.emotion_context.get("live_level", ""),
                        "risk_level": _risk.level,
                    },
                    snapshot=_conversation.TurnSnapshot(
                        turn_index=session.turn_index + 1,
                        user_text=asr_text,
                        stage=_stage_decision.stage,
                        goal_clear=_stage_decision.goal_clear,
                        action_ready=_stage_decision.action_ready,
                        should_summarize=_stage_decision.should_summarize,
                        summary_reason=_stage_decision.summary_reason,
                        risk_level=_risk.level,
                        risk_score=_risk.risk_score,
                        fusion_emotion=session.emotion_context.get("fusion_emotion_en"),
                        fusion_confidence=session.emotion_context.get("fusion_confidence"),
                    ),
                ), name=f"record-user:{sid}")

                # 【关键修正】情感分析完成后绝不因为"打断"而丢弃
                #   打断仅影响 TTS 播放，不影响 LLM 文本生成推进

                # ── 2) VLM 视觉理解（可选）──────────────────────────
                visual_context = ""
                if _vlm_future is not None:
                    # 早在多模态分析开始时已经并行触发，这里只为取结果
                    log.info("[VC] %s | [2/5] 等待 VLM 结果（已并行执行）", sid)
                    vlm_result = await _vlm_future
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
                    log.info("[VC] %s | [2/5] 跳过 VLM (is_visual=%s, has_frame=%s, vlm_avail=%s)",
                             sid, _is_visual_query(asr_text),
                             session.get_valid_frame() is not None, _vlm.is_available())

                # 【关键修正】VLM 完成后绝不因为"打断"而丢弃
                #   打断仅影响 TTS 播放，不影响 LLM 文本生成推进

                # ── 3) 构造 LLM 请求 ───────────────────────────────
                session.add_chat_message("user", asr_text)

                # 注意：history 只喂给 DeepSeek 兜底分支——Dify 走的是 dify_inputs +
                # sys.query，不读 history（见下方 _call_llm_stream）。所以这里可以放心
                # 把"Dify 侧靠提示词与开始节点变量实现的东西"补齐，不会与工作流重复。
                history: list[dict[str, str]] = [
                    {"role": "system", "content": _VC_SYSTEM_PROMPT},
                    # 教练方法论：与 Dify「普通心理教练」节点同源，避免兜底时掉档。
                    # 寒暄/极短输入用精简版——那类轮次用不上完整方法论，而且多发生在
                    # 通话最开始（提示词缓存尚未建立），省下的是真实的实时延迟。
                    {"role": "system", "content": _fallback.guide_for(
                        asr_text,
                        should_summarize=_stage_decision.should_summarize,
                        modality_conflict=bool(
                            session.emotion_context.get("needs_clarification")
                        ),
                    )},
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

                # 危机处置指令与对话历史都不在这里拼：前者要保持在所有上下文之后
                # （越靠后越有约束力），后者要保证「上下文在前、真实对话在后」。
                # 两者的追加位置见下方 3.5 节。

                # Dify 智能体入参（对应 chatflow start 节点变量：多模态情绪上下文）
                _emo_ctx = session.emotion_context
                _facial_cn = _emo_ctx.get("live_level", "")
                _facial_conf = float(_emo_ctx.get("live_score", 0) or 0)
                _voice_cn = voice_result.get("emotion_cn", "") if voice_result else ""
                _voice_conf = float(voice_result.get("confidence", 0) or 0) if voice_result else 0
                _text_cn = text_result.get("emotion_cn", "") if text_result else ""
                _text_conf = float(text_result.get("confidence", 0) or 0) if text_result else 0

                # 取回索引预热结果（失败不影响本轮通话）
                try:
                    await _kb_future
                except Exception as _e:
                    log.warning("[VC] %s | 知识库索引预热失败：%s", sid, _e)

                # ── 平台侧卡片知识库检索 ──────────────────────────
                # 用当前阶段与风险等级过滤后再注入：
                #   风险是硬约束（>= MEDIUM 时只放行 L0 安全卡）；
                #   阶段是软约束（不匹配降权 0.6，避免把好卡硬刷掉）。
                # 全程本地 jieba + BM25，不调用任何模型；失败只降级为"本轮不带资料"。
                try:
                    knowledge_context = await loop.run_in_executor(
                        None,
                        _partial(
                            _kb.context_block,
                            asr_text,
                            stage=_stage_decision.stage,
                            risk=_risk.level,
                        ),
                    )
                except Exception as _e:
                    log.warning("[VC] %s | 知识检索失败，本轮不带参考资料：%s", sid, _e)
                    knowledge_context = ""
                if knowledge_context:
                    log.info("[VC] %s | [1/5] 知识检索命中 %d 字参考资料（阶段=%s 风险=%s）",
                             sid, len(knowledge_context),
                             _stage_decision.stage, _risk.level)

                # ── 3.5) 兜底分支上下文补齐（只影响 DeepSeek 直连路径）──────
                # 过去 knowledge_context 与阶段判定只作为 Dify 的入参，
                # DeepSeek 分支拿不到，表现为「Dify 正常时像教练、一触发兜底就
                # 退回通用助手」。这里把它们拼进 history，使两条路径的回答依据一致。
                # 详见 app/services/ai_lab/fallback_prompt.py。
                history.extend(_fallback.build_context_messages(
                    stage=_stage_decision.stage,
                    goal_clear=_stage_decision.goal_clear,
                    action_ready=_stage_decision.action_ready,
                    should_summarize=_stage_decision.should_summarize,
                    summary_reason=_stage_decision.summary_reason,
                    knowledge_context=knowledge_context,
                    modality_conflict=bool(_emo_ctx.get("needs_clarification")),
                    modality_conflict_reason=str(_emo_ctx.get("conflict_reason") or ""),
                ))

                # 危机场景：注入安全处置指令。放在所有上下文之后，保证它是模型看到的
                # 最后一条规则（优先级最高，见 _CRISIS_SYSTEM_DIRECTIVE 的自述）。
                if session.emotion_context.get("risk_level") in (
                    _crisis.LEVEL_MEDIUM, _crisis.LEVEL_HIGH,
                ):
                    history.append({"role": "system", "content": _CRISIS_SYSTEM_DIRECTIVE})

                # 对话历史放最后：当前轮用户表达已在 get_chat_history() 末尾
                history.extend(session.get_chat_history()[-12:])
                log.info("[VC] %s | [3/5] LLM 请求构造完成, history=%d 条（含兜底上下文）",
                         sid, len(history))

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
                    # 平台侧阶段判定回传：工作流据此选择"继续教练 / 进入收束"，
                    # 使阶段口径只有一个权威来源（见 coach_stage_service）。
                    "current_stage": _stage_decision.stage,
                    "goal_clear": _stage_decision.goal_clear,
                    "action_ready": _stage_decision.action_ready,
                    # 开始节点把 should_summarize_hint 声明为 text-input（字符串），
                    # 工作流的条件分支用 contains 'true'（小写）匹配。
                    # 直接发 Python 布尔会被渲染成 "True"，大小写不匹配 →
                    # 平台的收束提示永远不生效，收束完全由 Dify 侧自行决定。
                    "should_summarize_hint": (
                        "true" if _stage_decision.should_summarize else "false"
                    ),
                    "platform_risk_level": _risk.level,
                    # 线索冲突信号：工作流可据此先澄清再回应
                    # （模态互相矛盾时才为 true，见 fusion_service._compute_conflict）
                    "modality_conflict": bool(_emo_ctx.get("needs_clarification")),
                    "modality_conflict_reason": _emo_ctx.get("conflict_reason", ""),
                    # 平台侧卡片知识库检索结果（本地 jieba + BM25，按阶段与风险过滤），
                    # 需在 Dify 开始节点声明同名变量并在提示词里引用，
                    # 见 docs/知识库检索方案.md
                    "knowledge_context": knowledge_context,
                }
                log.info("[VC] %s | Dify inputs: %s", sid, {
                    k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                    for k, v in dify_inputs.items() if v
                })

                # 按工作流声明的类型转换入参：变量被声明成 text-input 时，
                # JSON 布尔值会被 Dify 直接拒绝（"(type 'text-input') xxx must be a string"），
                # 整个工作流一步都不跑。详见 app/services/ai_lab/dify_service.py。
                # 只在真的要走 Dify 时查：DeepSeek 分支用不到 inputs，
                # 每次都查一次 /parameters 会在 Dify 网络慢时白等最多 DIFY_TIMEOUT 秒。
                if use_dify:
                    # normalize_inputs 内部是同步 requests.get（GET /parameters），
                    # 必须放进线程池执行：否则在缓存过期的那一轮，整个事件循环会被
                    # 冻结到请求返回为止——实测云端往返 1.7~6.8s，期间所有连接
                    # （不只是当前这通电话）全部卡住。其余耗时操作都用了 executor，
                    # 这里原先漏了。
                    dify_inputs = await loop.run_in_executor(
                        None, _dify.normalize_inputs, dify_inputs
                    )

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
                _llm_resp_t0: float | None = None

                _dify_base = _dify.api_base()

                def _call_llm_stream(provider: str, provider_key: str):
                    """在 executor 中调用 LLM 流式 API（Dify / DeepSeek），连接失败自动重试。"""
                    import time as _t
                    import requests as _requests
                    last_exc: Exception | None = None
                    for _attempt in range(1, _cfg.LLM_RETRIES + 1):
                        try:
                            if provider == "dify":
                                _dify_user = clients.get(sid, {}).get("user_id") or sid
                                log.info("[VC] %s | Dify HTTP POST -> %s/chat-messages (attempt %d/%d)",
                                         sid, _dify_base, _attempt, _cfg.LLM_RETRIES)
                                resp = _requests.post(
                                    f"{_dify_base}/chat-messages",
                                    headers={
                                        "Authorization": f"Bearer {provider_key}",
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
                                log.info("[VC] %s | Dify HTTP 响应: status=%s", sid, resp.status_code)
                                return resp
                            log.info("[VC] %s | DeepSeek HTTP POST -> %s/chat/completions | model=%s (attempt %d/%d)",
                                     sid, _cfg.DEEPSEEK_BASE_URL, _cfg.DEEPSEEK_MODEL, _attempt, _cfg.LLM_RETRIES)
                            _ds_payload: dict = {
                                "model": _cfg.DEEPSEEK_MODEL,
                                "messages": history,
                                "temperature": 0.7,
                                "max_tokens": _cfg.DEEPSEEK_MAX_TOKENS,
                                "stream": True,
                            }
                            # 关闭推理：详见 ai_lab/config.py 里 DEEPSEEK_DISABLE_REASONING 的说明。
                            # 不关的话，语音管线会先静默等推理，且正文可能被推理 token 挤空。
                            if _cfg.DEEPSEEK_DISABLE_REASONING:
                                _ds_payload["reasoning_effort"] = "none"
                            resp = _requests.post(
                                f"{_cfg.DEEPSEEK_BASE_URL}/chat/completions",
                                headers={
                                    "Authorization": f"Bearer {provider_key}",
                                    "Content-Type": "application/json",
                                },
                                json=_ds_payload,
                                timeout=_cfg.DEEPSEEK_TIMEOUT,
                                stream=True,
                            )
                            # 防御：个别账号/模型版本不认 reasoning_effort 会返回 400。
                            # 这种情况去掉该参数重发一次，而不是让整轮对话失败。
                            if (
                                resp.status_code == 400
                                and "reasoning_effort" in _ds_payload
                            ):
                                log.warning(
                                    "[VC] %s | DeepSeek 拒绝 reasoning_effort，去掉该参数重试",
                                    sid,
                                )
                                _ds_payload.pop("reasoning_effort", None)
                                resp = _requests.post(
                                    f"{_cfg.DEEPSEEK_BASE_URL}/chat/completions",
                                    headers={
                                        "Authorization": f"Bearer {provider_key}",
                                        "Content-Type": "application/json",
                                    },
                                    json=_ds_payload,
                                    timeout=_cfg.DEEPSEEK_TIMEOUT,
                                    stream=True,
                                )
                            log.info("[VC] %s | DeepSeek HTTP 响应: status=%s", sid, resp.status_code)
                            return resp
                        except Exception as _e:
                            last_exc = _e
                            log.warning("[VC] %s | %s 请求第 %d/%d 次失败: %s",
                                        sid, provider, _attempt, _cfg.LLM_RETRIES, _e)
                            if _attempt < _cfg.LLM_RETRIES:
                                _t.sleep(0.5 * _attempt)
                    raise last_exc  # type: ignore[misc]

                # 供应商顺序：配置了 Dify 就优先走 Dify；Dify 整轮没能产出任何内容时，
                # 自动降级到 DeepSeek 重试一次，避免 Dify 工作流抖动直接掐断整通电话。
                # （Dify 工作流的结构化输出解析失败会以 HTTP 400 或
                #   workflow_finished=failed 返回，见 docs/dify-工作流改动说明.md）
                _provider_plan = ["dify", "deepseek"] if use_dify else ["deepseek"]
                _provider_errors: list[str] = []
                _llm_is_dify = use_dify
                # Dify 工作流自判的风险等级（拿到后补写到本轮留痕，供两侧一致性统计）
                _dify_risk_level: str | None = None
                for _provider in _provider_plan:
                    if full_response.strip():
                        break
                    _llm_is_dify = _provider == "dify"
                    _provider_label = "Dify" if _llm_is_dify else "DeepSeek"
                    # Dify 工作流不稳定时先熔断，避免每轮通话都白等一次
                    if _llm_is_dify and _dify.circuit_open():
                        log.warning("[VC] %s | Dify 熔断中（%s），本轮跳过",
                                    sid, _dify.circuit_reason())
                        _provider_errors.append(f"Dify：熔断中（{_dify.circuit_reason()}）")
                        continue
                    _provider_key = (
                        _cfg.DIFY_API_KEY if _llm_is_dify else _cfg.DEEPSEEK_API_KEY or ""
                    ).strip()
                    if not _provider_key:
                        _provider_errors.append(f"{_provider_label}：未配置 API Key")
                        continue

                    try:
                        resp = await loop.run_in_executor(
                            None, _call_llm_stream, _provider, _provider_key
                        )
                    except Exception as _e:
                        log.error("[VC] %s | %s 调用失败 (executor): %s", sid, _provider_label, _e)
                        _provider_errors.append(f"{_provider_label}：连接失败")
                        if _llm_is_dify:
                            _dify.record_failure()
                        continue

                    if resp.status_code != 200:
                        err_msg = f"LLM API 返回 {resp.status_code}"
                        try:
                            err_body_txt = resp.text
                            log.error("[VC] %s | %s 错误响应体: %s",
                                      sid, _provider_label, err_body_txt[:500])
                            try:
                                err_body = resp.json()
                                err_msg = err_body.get("error", {}).get("message", err_msg)
                            except Exception:
                                err_msg = f"{err_msg}: {err_body_txt[:200]}"
                        except Exception:
                            pass
                        log.error("[VC] %s | %s 错误: %s", sid, _provider_label, err_msg)
                        _provider_errors.append(f"{_provider_label}：{err_msg}")
                        if _llm_is_dify:
                            _dify.record_failure()
                        continue

                    _llm_provider = _provider_label
                    log.info("[VC] %s | [4/5] 开始解析 %s SSE 流...", sid, _provider_label)
                    _outcome = await _consume_llm_stream(
                        sio, log, resp, is_dify=_llm_is_dify, sid=sid, session=session
                    )
                    full_response = _outcome["full_response"]
                    sentence_buffer = _outcome["sentence_buffer"]
                    token_count = _outcome["token_count"]
                    _first_token_t = _outcome["first_token_t"]
                    _first_tts_t = _outcome["first_tts_t"]
                    _llm_resp_t0 = _outcome["resp_t0"]
                    if _outcome.get("dify_risk_level"):
                        _dify_risk_level = _outcome["dify_risk_level"]
                    if _outcome["error"]:
                        _provider_errors.append(f"{_provider_label}：{_outcome['error']}")
                    if full_response.strip():
                        if _llm_is_dify:
                            _dify.record_success()
                    elif _llm_is_dify and not session.llm_cancelled:
                        _dify.record_failure()
                    if not full_response.strip() and not session.llm_cancelled:
                        log.warning("[VC] %s | %s 本轮未产出内容%s", sid, _provider_label,
                                    "，降级到下一个供应商重试"
                                    if _provider != _provider_plan[-1] else "")

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

                # Dify 自判风险等级补写留痕：等本轮快照落库后再 UPDATE 该行，
                # 这样"平台四级 vs Dify 三级"的一致性统计才有数据可比。
                if _dify_risk_level:
                    log.info("[VC] %s | 收到 Dify 自判风险等级: %s（补写本轮留痕）",
                             sid, _dify_risk_level)
                    spawn_task(
                        _backfill_dify_risk(_snapshot_task, sid, _dify_risk_level),
                        name=f"dify-risk:{sid}",
                    )
                elif (
                    full_response.strip()
                    and not _llm_is_dify
                    and _cfg.FALLBACK_RISK_JUDGE
                ):
                    # 本轮是兜底模型产出的：Dify 那边没有判定可写，就补一次同口径的
                    # 第二意见，否则"主路径抖了几轮"会直接从一致性统计里消失。
                    # 后台任务执行，不占用户这一轮的响应时间；详见 risk_judge 模块。
                    _recent_user_lines = [
                        str(_m.get("content") or "")
                        for _m in session.get_chat_history()[:-1]
                        if _m.get("role") == "user"
                    ][-3:]
                    _emotion_line = "；".join(
                        _p for _p in (
                            f"融合情绪：{session.emotion_context.get('fusion_emotion')}"
                            if session.emotion_context.get("fusion_emotion") else "",
                            f"面部表情：{session.emotion_context.get('live_level')}"
                            if session.emotion_context.get("live_level") else "",
                        ) if _p
                    )
                    spawn_task(
                        _backfill_fallback_risk(
                            loop, _snapshot_task, sid, asr_text,
                            recent_user_lines=_recent_user_lines,
                            emotion_line=_emotion_line,
                        ),
                        name=f"fallback-risk:{sid}",
                    )

                if not full_response.strip():
                    # 只有"完全没生成内容"才报错（真·空回复才是配置问题）
                    if not _was_interrupted:
                        _detail = "；".join(_provider_errors) if _provider_errors else ""
                        log.warning("[VC] %s | LLM 返回空内容（非打断导致）！%s", sid, _detail)
                        await sio.emit("vc_error", {
                            "stage": "llm" if _provider_errors else "llm_empty",
                            "message": (
                                f"AI 回复为空（{_detail}）"
                                if _detail else "AI 回复为空，请检查 API Key 是否有效或额度是否充足"
                            ),
                        }, room=sid)
                    else:
                        log.info("[VC] %s | LLM 被打断时还没生成内容（正常）", sid)
                else:
                    # 不管有没有被打断，只要生成了内容，就 emit 给前端显示
                    await sio.emit("vc_llm_done", {"full_response": full_response}, room=sid)
                    session.add_chat_message("assistant", full_response)

                    # ── 5) 会话留痕：补齐本轮 AI 回复与分段耗时 ──────────
                    #   尽力而为：写库失败只记日志，不影响用户体验（见 ai_conversation_service）。
                    session.turn_index += 1
                    _stage_ctx = session.stage_context
                    _now_ts = _time.time()
                    _turn_timings: dict[str, float] = {
                        "asr_seconds": _asr_seconds,
                        "multimodal_seconds": round(_mm_elapsed, 3),
                        "e2e_seconds": round(_now_ts - _turn_t0, 3),
                    }
                    if _llm_resp_t0:
                        _turn_timings["llm_total_seconds"] = round(_now_ts - _llm_resp_t0, 3)
                        if _first_token_t:
                            _turn_timings["llm_first_token_seconds"] = round(
                                _first_token_t - _llm_resp_t0, 3
                            )
                    if _first_token_t and _first_tts_t:
                        _turn_timings["tts_first_audio_seconds"] = round(
                            _first_tts_t - _first_token_t, 3
                        )
                    spawn_task(_conversation.record_message_safely(
                        session.conversation_id,
                        role="ASSISTANT",
                        content=full_response,
                        turn_index=session.turn_index,
                        snapshot=_conversation.TurnSnapshot(
                            turn_index=session.turn_index,
                            user_text=asr_text,
                            assistant_text=full_response,
                            stage=_stage_ctx.get("stage"),
                            goal_clear=_stage_ctx.get("goal_clear"),
                            action_ready=_stage_ctx.get("action_ready"),
                            should_summarize=_stage_ctx.get("should_summarize"),
                            summary_reason=_stage_ctx.get("summary_reason"),
                            risk_level=_risk.level,
                            risk_score=_risk.risk_score,
                            fusion_emotion=session.emotion_context.get("fusion_emotion_en"),
                            fusion_confidence=session.emotion_context.get("fusion_confidence"),
                            timings=_turn_timings,
                        ),
                        timings=_turn_timings,
                    ), name=f"record-assistant:{sid}")
                    log.info(
                        "[VC] %s | [5/5] 会话留痕 turn=%d conversation=%s | 耗时 %s",
                        sid, session.turn_index, session.conversation_id,
                        {k: v for k, v in _turn_timings.items()},
                    )

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
            else:
                # 回到监听状态
                session.state = realtime_session.STATE_LISTENING
                await sio.emit("vc_state_change", {"state": "listening"}, room=sid)
                session.reset_interrupt()
                log.info("[VC] %s | 状态重置 -> listening", sid)

    # ─── vc_start: 开始视频通话会话 ─────────────────────────────
    #: conversation_id -> 曾经承载过该通话的所有 sid。
    #: 断线重连时新连接会加入这些"房间"，从而接住在途回复：
    #: 若重连恰好发生在模型正在生成的那 20~30 秒里，回复是 emit 给旧 sid 的，
    #: 不做房间别名的话这一轮会凭空消失（服务端有回复、用户什么都听不到）。
    _conv_rooms: dict[int, set[str]] = {}

    @sio.on("vc_start")
    async def handle_vc_start(sid, data=None):

        from app.services.ai_lab import realtime_session
        from app.services import ai_conversation_service as _conversation_start

        session = realtime_session.get_session(sid)
        session.state = realtime_session.STATE_LISTENING
        session.touch()  # 通话开始即视为一次语音活动，空闲计时重新起算

        # Dify 入参声明后台预热：把这次联网挪出第一轮的关键路径（见函数注释）。
        spawn_task(_warm_dify_input_types(), name=f"dify-warm:{sid}")

        # 开会话 + 写授权存证（尽力而为：失败只记日志，不影响通话）
        payload = data if isinstance(data, dict) else {}
        consent = payload.get("consent")
        if not isinstance(consent, dict):
            # 旧客户端不传授权范围：保持"全模态"行为不变，
            # 但在存证里标明这是缺省授权，避免把默认值当成用户勾选。
            consent = {
                "mic": True,
                "camera": True,
                "multimodal": True,
                "basis": "legacy-client-default",
            }
        # 授权范围决定管线用哪些模态：未授权摄像头则不接收画面帧，
        # 未授权多模态则不把语音语调与面部计入情绪融合（缺省全开，兼容旧客户端）。
        if consent:
            session.consent_camera = bool(consent.get("camera", True))
            session.consent_multimodal = bool(consent.get("multimodal", True))
        # 断线重连：客户端会带回合话 id，优先接回原会话。
        # 否则一次通话会被拆成两条记录，且模型丢掉全部上下文（"教练突然失忆"）。
        if session.conversation_id is None and payload.get("conversation_id"):
            _resumed_id, _resumed_history = await _conversation_start.resume_session_safely(
                conversation_id=payload.get("conversation_id"),
                user_id=clients.get(sid, {}).get("user_id"),
                client_session_id=sid,
            )
            if _resumed_id:
                session.conversation_id = _resumed_id
                if _resumed_history and not session.chat_history:
                    for _m in _resumed_history:
                        session.add_chat_message(_m["role"], _m["content"])
                log.info(
                    "[VC] %s | 重连接回原会话=%s，回填历史 %d 条",
                    sid, _resumed_id, len(session.chat_history),
                )
        if session.conversation_id is None:
            session.conversation_id = await _conversation_start.start_session_safely(
                user_id=clients.get(sid, {}).get("user_id"),
                client_session_id=sid,
                consent=consent,
            )
        if session.conversation_id:
            clients.setdefault(sid, {})["ai_conv_id"] = session.conversation_id
            _cid = int(session.conversation_id)
            _rooms = _conv_rooms.setdefault(_cid, set())
            for _old_room in _rooms - {sid}:
                # 加入此前承载该通话的房间，承接在途的 vc_* 事件
                await sio.enter_room(sid, _old_room)
                log.info("[VC] %s | 加入历史连接房间 %s（会话=%s）", sid, _old_room, _cid)
            _rooms.add(sid)
        log.info("[VC] %s | 视频通话开始 | 会话=%s", sid, session.conversation_id)
        await sio.emit("vc_state_change", {"state": "listening"}, room=sid)
        if session.conversation_id:
            await sio.emit("vc_session_started", {
                "sessionId": session.conversation_id,
                "stage": "opening",
                "consent": {
                    "mic": True,
                    "camera": session.consent_camera,
                    "multimodal": session.consent_multimodal,
                },
            }, room=sid)
            # 前端据此把"结束通话 → 记录情绪日记"关联到本次会话
            await sio.emit("vc_conversation_ready", {
                "conversationId": session.conversation_id,
            }, room=sid)

    # ─── vc_consent: 通话中变更授权范围（开启 / 撤回摄像头、多模态）─────
    @sio.on("vc_consent")
    async def handle_vc_consent(sid, data=None):
        """用户在通话过程中重新授权或撤回授权。

        前端每次拨动「摄像头」「多模态线索」开关都会调用一次，服务端据此立刻改变
        管线行为，而不是等下一通电话：

        * 撤回摄像头 → 不再接收画面帧，已缓存的面部帧立刻清空；
        * 撤回多模态 → 语音语调与面部都不参与情绪融合，只按谈话内容判断。

        每次变更都写入 ``consent_records``（撤回会补 ``revoked_at``），
        使"什么时候授权、什么时候撤回"在伦理审核时可核对。
        """

        from app.services import ai_conversation_service as _conversation_consent
        from app.services.ai_lab import realtime_session

        if not realtime_session.has_session(sid):
            return
        session = realtime_session.get_session(sid)
        session.touch()  # 用户主动改授权 = 有人在操作，空闲计时重置

        payload = data if isinstance(data, dict) else {}
        scopes = payload.get("consent") if isinstance(payload.get("consent"), dict) else payload
        if not isinstance(scopes, dict):
            scopes = {}

        revoked: list[str] = []
        if "camera" in scopes:
            next_camera = bool(scopes["camera"])
            if session.consent_camera and not next_camera:
                revoked.append("camera")
            session.consent_camera = next_camera
        if "multimodal" in scopes:
            next_multimodal = bool(scopes["multimodal"])
            if session.consent_multimodal and not next_multimodal:
                revoked.append("multimodal")
            session.consent_multimodal = next_multimodal

        if not session.consent_camera:
            # 撤回即刻生效：丢弃最新帧并清空面部时序缓冲，
            # 保证本届及后续轮次都不会用到撤回前采集的画面。
            session.latest_frame = ""
            session.latest_frame_ts = 0.0
            facial_buffer.remove_client(sid)

        effective = {
            "mic": True,
            "camera": session.consent_camera,
            "multimodal": session.consent_multimodal,
        }
        log.info(
            "[VC] %s | 授权变更: camera=%s multimodal=%s | 本次撤回=%s",
            sid, effective["camera"], effective["multimodal"], "、".join(revoked) or "无",
        )

        consent_snapshot = {
            **effective,
            "basis": str(scopes.get("basis") or payload.get("basis") or "USER_TOGGLE")[:32],
        }
        raw_user_id = clients.get(sid, {}).get("user_id")
        try:
            user_id = int(raw_user_id) if raw_user_id is not None else None
        except (TypeError, ValueError):
            user_id = None
        spawn_task(
            _conversation_consent.record_consent_change_safely(
                user_id=user_id,
                client_session_id=sid,
                conversation_id=session.conversation_id,
                consent=consent_snapshot,
                revoked_scopes=revoked,
            ),
            name=f"consent-record:{sid}",
        )

        await sio.emit("vc_consent_updated", {
            "conversationId": session.conversation_id,
            "consent": effective,
            "revoked": revoked,
        }, room=sid)

    # ─── vc_stop: 结束视频通话会话 ─────────────────────────────
    @sio.on("vc_stop")
    async def handle_vc_stop(sid, data=None):
        from app.services.ai_lab import realtime_session
        from app.services import ai_conversation_service as _conversation_stop

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

        # 收尾：标记数据库会话结束（失败只记日志）
        if session.conversation_id:
            spawn_task(
                _conversation_stop.end_session_safely(session.conversation_id),
                name=f"end-session:{sid}",
            )
            log.info("[VC] %s | 视频通话结束（会话已归档 id=%s，turn=%d）",
                     sid, session.conversation_id, session.turn_index)
            session.conversation_id = None
            clients.get(sid, {}).pop("ai_conv_id", None)
        else:
            log.info("[VC] %s | 视频通话结束（会话已清理）", sid)
        # 通知前端状态变为 idle
        await sio.emit("vc_state_change", {"state": "idle"}, room=sid)
        # 延迟 2 秒后彻底移除会话（确保所有进行中的事件都已处理完毕）
        await asyncio.sleep(2)
        if realtime_session.has_session(sid):
            realtime_session.remove_session(sid)
            log.info("[VC] %s | 会话已彻底移除", sid)

    # ─── vc_audio_chunk: 接收音频分片 ──────────────────────────
    @sio.on("vc_audio_chunk")
    async def handle_vc_audio_chunk(sid, data):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            # 与 vc_audio_end 同理：静默丢弃会让"通话失效"完全不可观测。
            log.warning(
                "[VC] %s | 收到音频分片但通话会话不存在（多为断线重连），已丢弃", sid
            )
            return
        session = realtime_session.get_session(sid)
        session.touch()  # 收到音频分片 = 用户在场，空闲计时重置
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

    #: 上传接口接受的音频后缀，与 app/api/v1/ai_lab.py 的 _VC_AUDIO_SUFFIXES 保持一致
    _VC_AUDIO_EXTS = (".webm", ".webma", ".ogg", ".mp3", ".wav", ".opus")

    def _drop_uploaded(file_id: str) -> None:
        """删除已上传但不会被处理的音频。

        音频由 HTTP 先落盘、再由 socket 事件触发处理。当事件被丢弃
        （会话不存在、音频过小等）时文件就没人清理，会在临时目录里堆积。
        """
        if not file_id:
            return
        for _ext in _VC_AUDIO_EXTS:
            _p = _os.path.join(_VC_UPLOAD_DIR, f"{file_id}{_ext}")
            if _os.path.isfile(_p):
                try:
                    _os.remove(_p)
                except OSError as _e:
                    log.warning("[VC] 清理上传文件失败 %s：%s", _p, _e)
                return

    @sio.on("vc_audio_end")
    async def handle_vc_audio_end(sid, data=None):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            # 断线重连会拿到新的 sid，而通话会话绑在旧 sid 上。
            # 这里必须显式告知客户端补发 vc_start，否则用户会"说了话没有任何反应"：
            # 之前是静默 return，线上表现为通话无声失效、且服务端一行日志都没有。
            _lost_fid = ""
            if isinstance(data, dict):
                _lost_fid = str(data.get("file_id") or "")
            log.warning(
                "[VC] %s | 收到音频结束但通话会话不存在（多为断线重连），"
                "已请求客户端重建会话", sid,
            )
            _drop_uploaded(_lost_fid)
            await sio.emit("vc_error", {
                "stage": "session",
                "code": "SESSION_LOST",
                "recoverable": True,
                "message": "通话连接刚刚重连，请再说一次。",
            }, room=sid)
            return

        # 如果会话已关闭（用户点了结束通话），不再处理
        session = realtime_session.get_session(sid)
        if session.state == realtime_session.STATE_IDLE:
            log.info("[VC] %s | 会话已关闭，忽略音频处理", sid)
            return
        session.touch()  # 用户说完一轮 = 语音活动，空闲计时重置

        file_id = ""
        file_size = 0
        if isinstance(data, dict):
            file_id = data.get("file_id", "") or ""
            file_size = data.get("file_size", 0) or 0

        if not file_id:
            log.warning("[VC] %s | 缺少 file_id，忽略", sid)
            return

        # 只接受本平台生成的 32 位十六进制 file_id：
        #   - 挡住 "../" 之类的越权路径（拼路径前先做格式校验）
        #   - 避免把别的会话/任意本地文件当成用户音频送进 ASR
        if not UPLOAD_FILE_ID_PATTERN.match(file_id):
            log.warning("[VC] %s | file_id 格式非法，已拒绝: %r", sid, file_id[:64])
            await sio.emit("vc_error", {
                "stage": "upload", "message": "音频文件标识非法，请重新录制"
            }, room=sid)
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
            _drop_uploaded(file_id)
            return

        # 异步执行管线，不阻塞 socket 事件循环
        spawn_task(
            _run_video_call_pipeline_from_file(sid, audio_path),
            name=f"vc-pipeline:{sid}",
        )

    # ─── vc_interrupt: 用户打断 ────────────────────────────────
    @sio.on("vc_interrupt")
    async def handle_vc_interrupt(sid, data=None):
        from app.services.ai_lab import realtime_session
        if not realtime_session.has_session(sid):
            return
        session = realtime_session.get_session(sid)
        session.touch()  # 用户主动打断也是"人在场"的信号
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
            # 未授权摄像头画面时丢弃帧（前端本就不上传，这里是服务端兜底）
            if not session.consent_camera:
                return
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
