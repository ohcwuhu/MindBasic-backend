"""数据库会话：同步会话（Alembic/脚本/测试）与异步会话（应用运行时）。"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings


def _async_url() -> str:
    return settings.database_url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)


# 异步引擎与会话（应用运行时）
async_engine = create_async_engine(
    _async_url(),
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
