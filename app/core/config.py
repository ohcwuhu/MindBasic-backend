from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / backend/.env."""

    app_name: str = "MindBasic"
    debug: bool = False
    database_url: str = (
        "mysql+pymysql://mindbasic:MindBasic%402026@127.0.0.1:3306/mindbasic?charset=utf8mb4"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
