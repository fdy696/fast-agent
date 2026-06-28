import os
import secrets
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    VERSION: str = "1.0.0"
    APP_TITLE: str = "fast-agent"
    PROJECT_NAME: str = "fast-agent"
    APP_DESCRIPTION: str = "轻量 AI 应用后端模板"
    DEBUG: bool = True
    APP_ENV: str = "development"

    PROJECT_ROOT: str = str(Path(__file__).resolve().parents[1])
    BASE_DIR: str = str(Path(__file__).resolve().parents[2])
    LOGS_ROOT: str = str(Path(BASE_DIR) / "logs")
    DATETIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"

    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8080"
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    CORS_ALLOW_HEADERS: list[str] = ["Content-Type", "Authorization", "X-Requested-With"]

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/fast_agent"

    JWT_SECRET_KEY: str = Field(default_factory=lambda: os.getenv("SECRET_KEY") or secrets.token_urlsafe(32))
    SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 4
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    FIRST_SUPERUSER_USERNAME: str = "admin"
    FIRST_SUPERUSER_PASSWORD: str = "AdminPass123"
    FIRST_SUPERUSER_EMAIL: str | None = "admin@example.com"

    SWAGGER_UI_USERNAME: str = "admin"
    SWAGGER_UI_PASSWORD: str = "admin12345"

    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_TTL: int = 300

    # Rate limit — 默认 Redis 存储（多 worker 共享计数），Redis 不可用时自动 fallback 内存
    RATE_LIMIT_DEFAULT: str = "200/day,50/hour"
    RATE_LIMIT_STORAGE_URL: str = "redis://localhost:6379/0"

    # Agent / LLM
    API_KEY: str = ""           # DashScope/百炼 — MCP WebSearch、高德地图用
    DEEP_SEEK_API_KEY: str = "" # DeepSeek 聊天/压缩客户端独立 key
    APPCODE: str = ""
    AGENT_MODEL: str = "deepseek-v4-pro"
    AGENT_BASE_URL: str = "https://api.deepseek.com/v1"
    AGENT_MAX_STEPS: int = 12
    COMPRESS_MODEL: str = "deepseek-v4-flash"
    AGENT_CACHE_TTL: int = 7200
    MCP_TOOL_TIMEOUT: int = 15
    FEISHU_WEBHOOK_URL: str = ""
    TOKEN_COST_DAILY_LIMIT: float = 5.0

    # MCP server URLs
    MCP_WEB_SEARCH_URL: str = "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    MCP_AMAP_MAPS_URL: str = "https://dashscope.aliyuncs.com/api/v1/mcps/amap-maps/mcp"
    AMAP_API_KEY: str = ""
    MCP_WEATHER_URL: str = "https://ai.weiniai.cn/weather"
    MCP_SEARCH_IMAGE_URL: str = "https://ai.weiniai.cn/search-image"
    MCP_MAP_CRAWLER_URL: str = "https://ai.weiniai.cn/search-geocoder"

    @property
    def CORS_ORIGINS_LIST(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret_key(cls, value: str) -> str:
        if len(value) < 16:
            raise ValueError("JWT_SECRET_KEY 长度至少 16 字符")
        return value

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.SECRET_KEY and not self.JWT_SECRET_KEY:
            self.JWT_SECRET_KEY = self.SECRET_KEY
        if self.APP_ENV == "production" and self.DEBUG:
            raise ValueError("生产环境不能启用 DEBUG")


settings = Settings()
