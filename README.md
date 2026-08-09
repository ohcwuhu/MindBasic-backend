# MindBasic-backend

心理教练成长服务平台（MindBasic）后端。

## 技术栈

- FastAPI + SQLAlchemy 2.x + Alembic + MySQL 8.0

## 快速开始

```bash
conda activate relmind-backend        # 或使用其他 Python 3.12 环境
pip install -r requirements.txt
cp .env.example .env                  # 填写数据库连接
alembic upgrade head                  # 建表
uvicorn app.main:app --reload         # 启动开发服务
```

## 目录

- `app/`：应用代码（core 配置、db 会话、models、API 路由）
- `alembic/`：数据库迁移
- `docs/` 说明文档位于仓库上层 MindBasic/docs
