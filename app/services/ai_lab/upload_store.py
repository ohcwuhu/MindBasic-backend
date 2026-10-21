"""视频通话音频上传文件的目录与在途状态。"""

from pathlib import Path
import tempfile

UPLOAD_DIR = Path(tempfile.gettempdir()) / "vc_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_SUFFIXES = (".webm", ".webma", ".ogg", ".mp3", ".wav", ".opus")

_active_file_ids: set[str] = set()


def mark_active(file_id: str) -> None:
    if file_id:
        _active_file_ids.add(file_id)


def mark_done(file_id: str) -> None:
    _active_file_ids.discard(file_id)


def is_active(file_id: str) -> bool:
    return file_id in _active_file_ids


def find_path(file_id: str) -> Path | None:
    for suffix in AUDIO_SUFFIXES:
        candidate = UPLOAD_DIR / f"{file_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def drop(file_id: str) -> bool:
    """删除指定上传；返回是否实际删除了文件。"""
    path = find_path(file_id)
    if path is None:
        return False
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False
    finally:
        mark_done(file_id)
