"""数据库会话：同步会话（Alembic/脚本/测试）与异步会话（应用运行时）。"""

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


def async_url() -> str:
    """把同步驱动连接串转换为异步驱动连接串。

    应用、脚本、实验与测试共用同一处转换逻辑，避免各处拼接不一致。
    """
    return settings.database_url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


# 异步引擎与会话（应用运行时）
async_engine = create_async_engine(
    async_url(),
    pool_pre_ping=True,
    pool_recycle=3600,
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)

# 同步引擎与会话（Alembic / 脚本 / 测试清理）
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _set_utc_session(dbapi_connection, _connection_record) -> None:
    """连接建立后统一使用 UTC。

    ``CURRENT_TIMESTAMP`` / ``NOW()`` / ``CURDATE()`` 都随 MySQL 会话时区变化；
    只在序列化层补 ``Z`` 不能修正数据库默认值，因此必须从连接层统一。
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET time_zone = '+00:00'")
    finally:
        cursor.close()


event.listen(async_engine.sync_engine, "connect", _set_utc_session)
event.listen(engine, "connect", _set_utc_session)
