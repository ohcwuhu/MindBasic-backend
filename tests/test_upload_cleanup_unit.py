"""视频通话临时音频清理的纯单元回归。"""

import os
import time
import uuid

from app.services.ai_lab import upload_store
from app.services.maintenance_service import sweep_orphan_vc_uploads


def test_stale_vc_upload_is_removed_but_active_one_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_store, "UPLOAD_DIR", tmp_path)
    stale = tmp_path / f"{uuid.uuid4().hex}.wav"
    active = tmp_path / f"{uuid.uuid4().hex}.wav"
    stale.write_bytes(b"stale")
    active.write_bytes(b"active")
    old = time.time() - 2 * 60 * 60
    os.utime(stale, (old, old))
    os.utime(active, (old, old))

    upload_store.mark_active(active.stem)
    removed = sweep_orphan_vc_uploads(max_age_minutes=30)

    assert removed == 1
    assert not stale.exists()
    assert active.exists()

    upload_store.mark_done(active.stem)
    assert sweep_orphan_vc_uploads(max_age_minutes=30) == 1
    assert not active.exists()
