FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

# 运行数据目录（挂载持久化盘）
RUN mkdir -p /app/uploads /app/exports

EXPOSE 8000

# 生产：单 worker（AI 模型常驻），启动前自动迁移
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:socket_app --host 0.0.0.0 --port 8000 --workers 1"]
