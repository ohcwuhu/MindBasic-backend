# MindBasic 后端服务

心理教练成长服务平台（MindBasic）的 FastAPI 后端：为响应式 Web 前端提供 REST API，覆盖用户端自助工具、教练端工作台与管理后台。

## 技术栈

| 组件 | 选型 | 说明 |
| --- | --- | --- |
| Web 框架 | FastAPI | 自动 OpenAPI 文档（`/docs`） |
| ORM | SQLAlchemy 2.x（异步） | asyncmy 驱动，AsyncSession 应用层会话 |
| 迁移 | Alembic | 同步会话执行迁移，`compare_type=True` |
| 数据库 | MySQL 8（utf8mb4） | 本地开发默认 `mindbasic` 库 |
| 校验 | Pydantic v2 | 入参/出参统一模型，snake_case ↔ camelCase |
| 认证 | PyJWT + bcrypt | Access/Refresh 双令牌，Refresh 轮换 + httpOnly Cookie |
| 限流 | 可插拔 | 内存滑动窗口（默认）/ Redis 固定窗口 |
| 邮件 | smtplib | 邮箱验证码（登录/找回密码/绑定邮箱），支持 465 SSL / 587 STARTTLS |
| 测试 | pytest | 集成测试直连开发库，73 项覆盖核心链路 |

## 环境要求

- Python 3.12
- MySQL 8.0（本地或远程均可）
- （可选）Redis：仅在 `RATE_LIMIT_BACKEND=redis` 时使用

## 快速开始

```bash
# 1. 创建并激活虚拟环境（conda 示例）
conda create -n relmind-backend python=3.12 -y
conda activate relmind-backend

# 2. 安装依赖（含锁定版本）
cd backend
pip install -r requirements.txt
# 复现锁定版本：pip install -r requirements.lock

# 3. 创建数据库
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS mindbasic DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env：DATABASE_URL / JWT_SECRET_KEY 必填（缺失会启动失败）

# 5. 执行迁移（建表 + 种子数据：标签、自我教练模板、话术库、初始管理员、社群、测评量表）
alembic upgrade head

# 6. 可选：写入演示数据（2 个教练、示例内容）
python scripts/demo_seed.py

# 7. 启动开发服务（127.0.0.1:8000）
python scripts/run_dev.py
# 或 uvicorn app.main:socket_app --reload

# 8. 跑测试
pytest tests -q
```

> 初始管理员：`13800138000 / Admin@123456`（首次登录后请修改密码）。

## 环境变量（.env）

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 是 | `mysql+pymysql://用户:密码@主机:3306/mindbasic?charset=utf8mb4`，密码含特殊字符需 URL 编码 |
| `JWT_SECRET_KEY` | 是 | 随机 64 位 hex，缺失或占位值启动失败 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | Access Token 有效期，默认 120 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | 否 | Refresh Token 有效期，默认 14 |
| `COOKIE_SECURE` | 否 | 生产（DEBUG=false）强制 true |
| `CORS_ORIGINS` | 否 | 生产环境需显式配置前端域名 |
| `RATE_LIMIT_BACKEND` | 否 | `memory`（单实例）/ `redis`（多实例，需 `REDIS_URL`） |
| `EMAIL_ENABLED` | 否 | false 时验证码打印在后端日志，方便开发联调 |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | 否 | 邮箱发送；QQ/163 用 465（SSL），STARTTLS 服务用 587 |

## 目录结构

```
backend/
├── alembic/            # 迁移脚本（版本链 + 种子数据）
├── app/
│   ├── api/v1/         # 路由层（auth、users、coaches、coach、appointments、articles、
│   │                   #   emotion_journals、self_coaching、checkins、communities、
│   │                   #   growth_assessments、notifications、files、home、admin、platform…）
│   ├── core/           # 配置、异常、安全、限流、缓存、日志
│   ├── db/             # 引擎与会话（async 应用 / sync 迁移与脚本）
│   ├── models/         # SQLAlchemy 模型（user、coach、content、growth、community、v1_1…）
│   ├── schemas/        # Pydantic 请求/响应模型
│   ├── services/       # 业务逻辑层（认证、预约、个案、社群、测评、邮件、内容合规…）
│   └── utils/          # 时间、格式化等工具
├── scripts/
│   ├── run_dev.py          # 开发启动
│   ├── demo_seed.py        # 演示数据
│   └── cleanup_orphan_files.py  # 清扫上传孤儿文件（支持 --dry-run）
├── tests/              # pytest 集成测试（16 个文件，73 项）
├── requirements.txt    # 直接依赖
└── requirements.lock   # pip-compile 锁定（含 asyncmy）
```

## 功能模块

- **账号**：手机号注册/登录、邮箱验证码登录、找回密码、绑定/换绑邮箱、Token 刷新、登出、注销、Access Token 黑名单
- **自助工具**：自我教练（5 模板四步流程 + 成长行动卡）、情绪日记（预设话术 + 趋势 + 月度心情日历）
- **教练服务**：教练目录/详情/评价、在线预约（防超卖 + 幂等键）、我的预约、评价
- **教练端**：入驻审核、预约管理、个案记录（Markdown + 导出）、服务/时段管理、客户管理（含待跟进提醒）、话术库、收到的评价、社群管理
- **科普**：文章分类/详情/收藏、首页聚合（匿名缓存）
- **成长体系**：每日打卡、排行榜、勋章、成长测评（资源导向，不诊断）
- **社群**：主题社群、加入/退出、帖子/评论/点赞、教练置顶治理、管理员上下架
- **通知**：站内消息（预约、审核等）
- **管理后台**：用户（含注册时间筛选）、教练审核、文章/分类/轮播/标签/话术库、平台配置（热线/免责声明）、社群上下架、概览统计
- **文件**：通用上传（头像/证书/身份证），私有文件访问控制

## API 约定

- 统一前缀：`/api/v1`；统一响应：`{ code, message, data, traceId }`
- 错误码：业务错误 `{ status, code, message }`，如 `PHONE_EXISTS`、`CODE_INVALID`、`RATE_LIMITED`
- 分页：`{ items, pagination: { page, pageSize, totalItems, totalPages, hasMore } }`
- 鉴权：`Authorization: Bearer <accessToken>`；Refresh Token 走 httpOnly Cookie（`/api/v1/auth/refresh`）
- 在线文档：`http://127.0.0.1:8000/docs`

主要端点：

| 分组 | 端点示例 |
| --- | --- |
| 认证 | `/auth/register` `/auth/login` `/auth/email-code` `/auth/email-login` `/auth/reset-password` `/auth/refresh` `/auth/logout` |
| 用户 | `/users/me` `/users/me/email` `/users/me/favorites` `/users/me/badges` |
| 自助工具 | `/self-coaching/templates|records` `/emotion-journals` `/emotion-journals/trend|calendar` |
| 教练服务 | `/coaches` `/coaches/{id}/slots|reviews` `/appointments` |
| 教练端 | `/coach/profile|services|slots|clients|appointments|cases|reviews|phrases` |
| 科普/首页 | `/articles` `/home` `/platform/config` |
| 成长 | `/check-ins` `/check-ins/leaderboard` `/growth-assessments` |
| 社群 | `/communities` `/communities/{id}/posts` `/communities/{id}/posts/{postId}/comments|like` |
| 后台 | `/admin/users|coach-audits|articles|banners|tags|feedback-lib|communities|system-configs|stats` |

## 数据库与迁移

- 表结构由 `app/models/` 定义，迁移统一走 Alembic（版本链见 `alembic/versions/`）；
- 新增字段/表：修改模型后 `alembic revision --autogenerate -m "..."`，检查脚本后 `alembic upgrade head`；
- 种子数据（标签、5 套模板与问句、话术库、初始管理员、社群、测评量表）在各迁移中插入，可随版本演进；
- 注意：MySQL DDL 非事务，迁移失败后先清理残留对象再重跑。

## 测试

```bash
cd backend
pytest tests -q          # 全量 73 项
pytest tests/test_auth.py -q   # 单模块
```

- 测试直连开发库，使用唯一手机号并在 teardown 清理；跑完建议清一下 `email_verification_codes` 等临时表避免冷却误伤：
  ```sql
  DELETE FROM email_verification_codes;
  ```
- `conftest.py` 提供 `client` / `auth_headers` / `admin_headers` 模块级夹具；
- 邮箱验证码测试通过打桩 `email_service.send_email` 捕获验证码，不依赖真实 SMTP。

## 部署

```bash
# 生产：多 worker + HTTPS 反代
uvicorn app.main:socket_app --host 0.0.0.0 --port 8000 --workers 1
```

- 生产环境配置：`DEBUG=false`、`COOKIE_SECURE=true`、显式 `CORS_ORIGINS`；
- 多实例部署：`RATE_LIMIT_BACKEND=redis` 并配置 `REDIS_URL`（黑名单/首页缓存同样建议切 Redis，接口已抽象）；
- Nginx 将 `/api` 反向代理到后端，静态资源交给前端静态托管/CDN；
- 邮件：配置 SMTP 后 `EMAIL_ENABLED=true`；验证码在服务端校验（哈希存储、一次性、60s 冷却、5 次错误作废）。

## AI 实验室（情绪识别 + AI 心理教练）

Mind2/RelMind 已整体并入本仓库，不再单独运行：

- SocketIO 实时情绪识别：`/socket.io/`（upload_frame → emotion_result），由 `app.services.ai_lab.socket_events` 实现；
- 多模态音频分析：`POST /api/analyze_audio`、`GET /api/analyze_audio/config_check`、`POST /api/analyze_audio/warmup`；
- AI 心理教练：`POST /api/ai_coach/chat`（DeepSeek，识别结果作为上下文引导）。

启动注意：

- 必须通过 `app.main:socket_app` 启动（FastAPI + SocketIO 共用 ASGI 应用），`scripts/run_dev.py` 已配置；
- AI 实验室建议单进程运行（模型常驻内存约 3~4 GB，`--workers 1`）；
- 首次启动会在后台自动预热模型（SenseVoice / emotion2vec / mDeBERTa / OpenSMILE），可通过 `config_check` 查看加载状态；
- AI 教练需要 `.env` 中配置 `DEEPSEEK_API_KEY`（可选，未配置时该接口返回 503）。

重型 AI 依赖（torch / tensorflow / funasr / opensmile / deepface）见 `requirements-ai.txt`。

## 常见问题

- **启动报“配置校验失败”**：检查 `DATABASE_URL`、`JWT_SECRET_KEY`；生产环境检查 `COOKIE_SECURE`、`CORS_ORIGINS`。
- **迁移报“Duplicate column”**：MySQL DDL 非事务，清理已创建对象后重跑。
- **验证码收不到**：`EMAIL_ENABLED=false` 时看后端日志；true 时检查授权码/端口（QQ 465 SSL）。
- **时区**：应用按 UTC 存储（`utcnow_naive`），数据库服务器时间可能为本地时间；涉及跨时区比较的新逻辑请统一使用 `utcnow_naive`。
