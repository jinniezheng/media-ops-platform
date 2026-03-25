from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "MediaOps Platform"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./media_ops.db"

    # Redis (reserved for future use)
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: list[str] = [
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:3000",
        "http://192.168.6.188:5174",
        "http://192.168.103.207:5175",
    ]

    # Bilibili
    BILIBILI_API_BASE: str = "https://api.bilibili.com"
    BILIBILI_COOKIE: str = ""

    # XHS (Xiaohongshu)
    XHS_COOKIES: str = ""

    # LLM (NVIDIA OpenAI-compatible)
    # LLM_API_KEY: str = "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"
    # LLM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    # LLM_MODEL: str = "minimaxai/minimax-m2.1"


    LLM_API_KEY: str = "ms-ab8b7e9c-3d90-4fd8-a507-e5bbaf00f10b"
    LLM_BASE_URL: str = "https://api-inference.modelscope.cn/v1"
    LLM_MODEL: str = "Qwen/Qwen3.5-35B-A3B"

    # JWT Auth
    JWT_SECRET: str = "change-me-in-production-use-env"
    JWT_EXPIRE_HOURS: int = 72

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
