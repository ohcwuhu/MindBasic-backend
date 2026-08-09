from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / backend/.env."""

    app_name: str = "MindBasic"
    debug: bool = False
    database_url: str = (
        "mysql+pymysql://mindbasic:MindBasic%402026@127.0.0.1:3306/mindbasic?charset=utf8mb4"
    )
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 14
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def cors_origin_list() -> list[str]:
    return [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
