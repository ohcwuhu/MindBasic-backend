import time
import uuid
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
import socketio
from sqlalchemy import text

from app.api.v1 import (
    admin,
    admin_audit,
    admin_crisis,
    admin_orders,
    ai_conversations,
    appointments,
    articles,
    auth,
    coach,
    coaches,
    checkins,
    communities,
    emotion_journals,
    files,
    growth_assessments,
    home,
    notifications,
    orders,
    platform,
    self_coaching,
    tags,
    users,
    wallet,
)
from app.api.v1 import ai_coach, ai_lab
from app.api.v1 import chat as chat_api
from app.services.ai_lab import socket_events
from app.services.chat_socket import register_chat_socket_events
from app.core.config import cors_origin_list, settings
from app.core.exceptions import AppError
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS, metrics_body
from app.core.logging import get_logger, setup_logging
from app.core.scheduler import shutdown_scheduler, start_scheduler
from app.db.session import AsyncSessionLocal

setup_logging()
logger = get_logger("mindbasic")
access_logger = get_logger("mindbasic.access")


def _background_warmup() -> None:
    """后台预热 AI 模型（SenseVoice + emotion2vec + 文本情感 + OpenSMILE + TTS + VLM）。"""
    try:
        from app.api.v1.ai_lab import _SV, _EV, _TE, _OS

        logger.info("[WARMUP] 开始后台预热 AI 实验室全部模型 ...")
        for name, svc in [
            ("sensevoice", _SV),
            ("emotion2vec", _EV),
            ("text_emotion", _TE),
            ("opensmile", _OS),
        ]:
            try:
                svc.warmup()
            except Exception as e:  # noqa: BLE001
                logger.warning("[WARMUP] %s 预热失败: %s", name, e)

        # 视频通话：TTS + VLM 预热
        try:
            from app.services.ai_lab import tts_service, vlm_service
            tts_service.warmup()
            vlm_service.warmup()
        except Exception as e:  # noqa: BLE001
            logger.warning("[WARMUP] TTS/VLM 预热失败: %s", e)

        logger.info("[WARMUP] 后台预热完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("[WARMUP] 预热流程异常: %s", e)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """服务启动后后台预热 AI 模型 + 启动定时任务；测试导入不受影响。"""
    threading.Thread(target=_background_warmup, daemon=True).start()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_trace_id(request: Request, call_next):
    trace_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    request.state.trace_id = trace_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-Id"] = trace_id
    access_logger.info(
        "access",
        extra={
            "extra": {
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "durationMs": round((time.perf_counter() - started) * 1000, 2),
                "traceId": trace_id,
                "userId": getattr(request.state, "user_id", None),
                "ip": request.client.host if request.client else None,
            }
        },
    )
    return response


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(self)"
    if not settings.debug:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def add_metrics(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        route = getattr(request.scope.get("route"), "path", request.url.path)
        HTTP_REQUESTS.labels(request.method, route, "500").inc()
        HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - start)
        raise
    route = getattr(request.scope.get("route"), "path", request.url.path)
    HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - start)
    return response


def envelope(status_code: int, code: str, message: str, data, trace_id: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": data, "traceId": trace_id},
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return envelope(
        exc.status_code,
        exc.code,
        exc.message,
        exc.data,
        getattr(request.state, "trace_id", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = []
    for err in exc.errors():
        loc = [str(p) for p in err["loc"] if p not in ("body", "query", "path", "header")]
        errors.append({"field": ".".join(loc) if loc else "body", "message": err["msg"]})
    return envelope(
        400,
        "VALIDATION_ERROR",
        "请求参数校验失败",
        {"errors": errors},
        getattr(request.state, "trace_id", None),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled error",
        extra={
            "extra": {
                "method": request.method,
                "path": request.url.path,
                "traceId": getattr(request.state, "trace_id", None),
            }
        },
    )
    return envelope(
        500,
        "INTERNAL_ERROR",
        "服务器开小差了，请稍后重试",
        None,
        getattr(request.state, "trace_id", None),
    )


app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(self_coaching.router, prefix="/api/v1")
app.include_router(emotion_journals.router, prefix="/api/v1")
app.include_router(home.router, prefix="/api/v1")
app.include_router(coach.router, prefix="/api/v1")
app.include_router(coach.phrase_library_router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(appointments.router, prefix="/api/v1")
app.include_router(coaches.router, prefix="/api/v1")
app.include_router(tags.router, prefix="/api/v1")
app.include_router(files.router, prefix="/api/v1")
app.include_router(growth_assessments.router, prefix="/api/v1")
app.include_router(checkins.router, prefix="/api/v1")
app.include_router(communities.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(platform.router, prefix="/api/v1")
app.include_router(articles.articles_router, prefix="/api/v1")
app.include_router(articles.categories_router, prefix="/api/v1")
app.include_router(admin.users_router, prefix="/api/v1")
app.include_router(admin.articles_router, prefix="/api/v1")
app.include_router(admin.categories_router, prefix="/api/v1")
app.include_router(admin.banners_router, prefix="/api/v1")
app.include_router(admin.tags_router, prefix="/api/v1")
app.include_router(admin.feedback_router, prefix="/api/v1")
app.include_router(admin.stats_router, prefix="/api/v1")
app.include_router(admin.config_router, prefix="/api/v1")
app.include_router(admin.communities_router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(wallet.router, prefix="/api/v1")
app.include_router(admin_orders.router, prefix="/api/v1")
app.include_router(admin_orders.wallet_router, prefix="/api/v1")
app.include_router(admin_audit.router, prefix="/api/v1")
app.include_router(admin_crisis.router, prefix="/api/v1")
app.include_router(ai_conversations.router, prefix="/api/v1")

# ─── AI 实验室：多模态音频分析 + AI 心理教练 ──────────────────────────
app.include_router(ai_lab.router)
app.include_router(ai_coach.router)
app.include_router(chat_api.router, prefix="/api/v1")
logger.info("[INIT] AI 实验室路由已挂载（/api/analyze_audio, /api/ai_coach/chat）")

# ─── AI 实验室：SocketIO 实时情绪识别 ────────────────────────────────
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)
socket_events.register_socket_events(sio, logger)
register_chat_socket_events(sio, logger)

# SocketIO 与 FastAPI 共用同一 ASGI 应用（uvicorn 需以 socket_app 启动）
socket_app = socketio.ASGIApp(sio, app)


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
async def health_ready() -> JSONResponse:
    """就绪探针：数据库连通性检查。"""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "degraded", "db": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "db": "ok"})


@app.get("/metrics", include_in_schema=False, tags=["system"])
async def metrics() -> Response:
    body, content_type = metrics_body()
    return Response(content=body, media_type=content_type)
