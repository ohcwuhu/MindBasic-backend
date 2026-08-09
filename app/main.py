import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import auth, emotion_journals, self_coaching, users
from app.core.config import cors_origin_list, settings
from app.core.exceptions import AppError

logger = logging.getLogger("mindbasic")

app = FastAPI(title=settings.app_name, debug=settings.debug)

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
    response = await call_next(request)
    response.headers["X-Request-Id"] = trace_id
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
    logger.exception("unhandled error: %s", exc)
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


@app.get("/health", tags=["system"])
def health() -> dict:
    return {"status": "ok"}
