import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

# 测试环境关闭生产调度器，避免定时任务在测试进程内触发
os.environ["SCHEDULER_ENABLED"] = "false"

# 数据库测试必须显式指向独立测试库。缺少该变量时直接阻止运行，
# 避免 -q / IDE 的默认 pytest 命令误连日常开发库。
test_database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
# GitHub Actions 的 MySQL service 本身就是一次性隔离库；兼容当前线上 workflow
# 仅提供 DATABASE_URL 的配置。本地仍只接受显式的 TEST_DATABASE_URL。
if not test_database_url and os.environ.get("CI", "").lower() == "true":
    test_database_url = os.environ.get("DATABASE_URL", "").strip()
if not test_database_url:
    raise RuntimeError(
        "数据库集成测试需要 TEST_DATABASE_URL（独立测试库）。"
        "纯单元测试请使用 --noconftest。"
    )
os.environ["DATABASE_URL"] = test_database_url

from app.db.session import SessionLocal
from app.db import session as db_session
from app.models.user import User

# ---------------------------------------------------------------------------
# 测试用引擎：改用 NullPool
# ---------------------------------------------------------------------------
# 应用运行在一个长期存活的事件循环里，连接池可以正常复用；而 TestClient 会为每个
# 测试模块创建并销毁自己的事件循环，全局池中的连接便可能在"旧循环建立、新循环回收"，
# 触发 asyncmy 的 "Event loop is closed"（Windows + Python 3.13 下尤为明显）。
# 测试改用 NullPool：连接随用随开、在当前循环内释放，从根上避免跨循环回收。
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

_test_engine = create_async_engine(
    db_session.async_url(),
    poolclass=NullPool,
    pool_pre_ping=True,
)
event.listen(_test_engine.sync_engine, "connect", db_session._set_utc_session)
db_session.async_engine = _test_engine
db_session.AsyncSessionLocal.configure(bind=_test_engine)

from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_rate_limits():
    from app.core.rate_limit import reset_rate_limits
    from app.core.token_blacklist import reset_blacklist
    from app.core.cache import reset_cache

    reset_rate_limits()
    reset_blacklist()
    reset_cache()
    yield
    reset_rate_limits()
    reset_blacklist()
    reset_cache()


def unique_phone() -> str:
    return "139" + str(int(time.time() * 1000) % 100000000).zfill(8)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers(client):
    phone = unique_phone()
    resp = client.post(
        "/api/v1/auth/register",
        json={"phone": phone, "password": "Test123456", "nickname": "接口测试", "privacyAgreed": True, "serviceAgreed": True},
    )
    assert resp.status_code == 201
    token = resp.json()["data"]["accessToken"]
    yield {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.phone == phone))
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def admin_headers(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138000", "password": "Admin@123456"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['data']['accessToken']}"}
