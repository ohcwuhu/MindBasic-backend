"""通用文件上传（头像、资质证书、身份证等）。"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_optional_user
from app.api.response import ok
from app.core.exceptions import AppError
from app.models.file import FileUpload
from app.models.user import User

UPLOAD_DIR = Path(__file__).resolve().parents[3] / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
MAX_SIZE = 5 * 1024 * 1024  # 5MB

router = APIRouter(prefix="/files", tags=["files"])


@router.post("", status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    usage: str = Form(default="general"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES:
        raise AppError(400, "FILE_TYPE_INVALID", "仅支持 JPG/PNG/WebP/PDF 文件")
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise AppError(400, "FILE_TOO_LARGE", "文件大小不能超过 5MB")
    if not data:
        raise AppError(400, "FILE_TYPE_INVALID", "文件内容为空")

    original_name = (file.filename or "file").replace("\\", "/").split("/")[-1][:255]
    filename = f"{uuid.uuid4().hex}{ALLOWED_TYPES[content_type]}"
    path = UPLOAD_DIR / filename
    path.write_bytes(data)
    is_private = usage == "idcard"
    record = FileUpload(
        file_id=filename,
        user_id=user.id,
        usage=usage,
        is_private=is_private,
        original_name=original_name,
        content_type=content_type,
        size=len(data),
    )
    db.add(record)
    try:
        db.commit()
        db.refresh(record)
    except Exception:
        # 入库失败时清理已写盘的文件，避免孤儿文件
        path.unlink(missing_ok=True)
        db.rollback()
        raise
    return ok(
        {
            "fileId": record.file_id,
            "url": f"/api/v1/files/{record.file_id}/content",
            "isPrivate": is_private,
            "originalName": record.original_name,
        },
        trace_id=request.state.trace_id,
    )


@router.get("/{file_id}/content")
def download_file(
    file_id: str,
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    record = db.scalar(select(FileUpload).where(FileUpload.file_id == file_id))
    if record is None:
        raise AppError(404, "NOT_FOUND", "文件不存在")
    if record.is_private:
        if user is None:
            raise AppError(401, "UNAUTHORIZED", "请先登录")
        if record.user_id != user.id and user.role != "ADMIN":
            raise AppError(403, "FORBIDDEN", "无权访问该文件")
    path = UPLOAD_DIR / record.file_id
    if path.name != record.file_id or not path.is_file():
        raise AppError(404, "NOT_FOUND", "文件不存在")
    return FileResponse(path, media_type=record.content_type)
