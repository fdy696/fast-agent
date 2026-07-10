import os, secrets
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore")
    VERSION: str="1.0.0"
    APP_TITLE: str="fast-agent"
    PROJECT_NAME: str="fast-agent"
    APP_DESCRIPTION: str="轻量 AI 应用后端模板"
    DEBUG: bool=True
    APP_ENV: str="development"
    PROJECT_ROOT: str=str(Path(__file__).resolve().parents[1])
    BASE_DIR: str=str(Path(__file__).resolve().parents[2])
    LOGS_ROOT: str=str(Path(BASE_DIR) / "logs")
    DATETIME_FORMAT: str="%Y-%m-%d %H:%M:%S"
    CORS_ORIGINS: str="http://localhost:3000,http://localhost:8080"
    CORS_ALLOW_CREDENTIALS: bool=True
    CORS_ALLOW_METHODS: list[str]=["GET","POST","PUT","DELETE","OPTIONS"]
    CORS_ALLOW_HEADERS: list[str]=["Content-Type","Authorization","X-Requested-With"]
    DATABASE_URL: str="postgresql+asyncpg://postgres:postgres@localhost:5432/fast_agent"
    JWT_SECRET_KEY: str=Field(default_factory=lambda: os.getenv("SECRET_KEY") or secrets.token_urlsafe(32))
    SECRET_KEY: str|None=None
    JWT_ALGORITHM: str="HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int=60*4
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int=7
    FIRST_SUPERUSER_USERNAME: str="admin"
    FIRST_SUPERUSER_PASSWORD: str="AdminPass123"
    FIRST_SUPERUSER_EMAIL: str|None="admin@example.com"
    SWAGGER_UI_USERNAME: str="admin"
    SWAGGER_UI_PASSWORD: str="admin12345"
    REDIS_URL: str="redis://localhost:6379/0"
    CACHE_TTL: int=300
    RATE_LIMIT_DEFAULT: str="200/day,50/hour"
    RATE_LIMIT_STORAGE_URL: str="redis://localhost:6379/0"
    API_KEY: str=""
    DEEP_SEEK_API_KEY: str=""
    APPCODE: str=""
    AGENT_NAME: str="fast-agent"
    AGENT_PROVIDER: str="DeepSeek"
    AGENT_MODEL: str="deepseek-v4-pro"
    AGENT_PROVIDER_DISPLAY: str="DeepSeek"
    AGENT_MODEL_DISPLAY: str="DeepSeek V4"
    AGENT_BASE_URL: str="https://api.deepseek.com/v1"
    AGENT_MAX_STEPS: int=12
    AGENT_TOKEN_LIMIT: int=300_000
    COMPRESS_MODEL: str="deepseek-v4-flash"
    AGENT_CACHE_TTL: int=7200
    MCP_TOOL_TIMEOUT: int=15
    FEISHU_WEBHOOK_URL: str=""
    SUMMARY_TRIGGER_TURNS: int=12
    SUMMARY_TRIGGER_ESTIMATED_TOKENS: int=10_000
    KEEP_RECENT_TURNS: int=6
    MAX_SUMMARY_CHARS: int=1_500
    MCP_WEB_SEARCH_URL: str="https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    MCP_AMAP_MAPS_URL: str="https://dashscope.aliyuncs.com/api/v1/mcps/amap-maps/mcp"
    AMAP_API_KEY: str=""
    MCP_WEATHER_URL: str="https://ai.weiniai.cn/weather"
    MCP_SEARCH_IMAGE_URL: str="https://ai.weiniai.cn/search-image"
    MCP_MAP_CRAWLER_URL: str="https://ai.weiniai.cn/search-geocoder"
    @property
    def CORS_ORIGINS_LIST(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret_key(cls, value: str) -> str:
        if len(value) < 16:
            raise ValueError("JWT_SECRET_KEY length must be >= 16")
        return value
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.SECRET_KEY and not self.JWT_SECRET_KEY:
            self.JWT_SECRET_KEY=self.SECRET_KEY
        if self.APP_ENV=="production" and self.DEBUG:
            raise ValueError("Cannot enable DEBUG in production")

settings=Settings()
