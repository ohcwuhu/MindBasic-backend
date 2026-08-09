"""通用文件上传（头像、资质证书、身份证等）。"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.response import ok
from app.core.exceptions import AppError
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

    filename = f"{uuid.uuid4().hex}{ALLOWED_TYPES[content_type]}"
    (UPLOAD_DIR / filename).write_bytes(data)
    is_private = usage == "idcard"
    return ok(
        {
            "fileId": filename,
            "url": f"/uploads/{filename}",
            "isPrivate": is_private,
        },
        trace_id=request.state.trace_id,
    )
