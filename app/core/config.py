from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / backend/.env."""

    app_name: str = "MindBasic"
    debug: bool = False
    # 不提供默认凭据：必须由环境变量 / .env 提供，缺失则启动失败
    database_url: str = ""
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 14
    cookie_secure: bool = False
    log_level: str = "INFO"
    rate_limit_backend: str = "memory"
    redis_url: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # 邮箱验证码（短信替代方案）：默认关闭，开启后走 SMTP
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    email_code_ttl_minutes: int = 10
    # AI 实验室：DeepSeek 心理教练
    deepseek_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def _validate_settings() -> None:
    """启动前校验关键配置，避免生产环境使用弱默认值。"""
    problems: list[str] = []
    if not settings.database_url:
        problems.append("DATABASE_URL 未配置")
    if not settings.jwt_secret_key or settings.jwt_secret_key.lower() in {
        "change-me-in-production",
        "changeme",
        "secret",
    }:
        problems.append("JWT_SECRET_KEY 未配置或仍为占位值")
    if not settings.debug:
        if not settings.cookie_secure:
            problems.append("生产环境（DEBUG=false）必须设置 COOKIE_SECURE=true")
        if settings.cors_origins == "http://localhost:5173,http://127.0.0.1:5173":
            problems.append("生产环境请显式配置 CORS_ORIGINS")
    if problems:
        raise RuntimeError("配置校验失败：\n- " + "\n- ".join(problems))


_validate_settings()


def cors_origin_list() -> list[str]:
    return [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
