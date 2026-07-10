from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.dependency import get_current_username
from core.init_app import (
    init_data,
    make_middlewares,
    register_exceptions,
    register_routers,
)
from core.rate_limit import limiter
from db.session import close_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_data()
    yield
    # Agent 关闭
    from agent.mcp import shutdown_mcp_toolsets

    await shutdown_mcp_toolsets()
    from utils.feishu import close_feishu

    await close_feishu()
    from utils.cache import cache_manager

    await cache_manager.disconnect()
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_TITLE,
        description=settings.APP_DESCRIPTION,
        version=settings.VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        middleware=make_middlewares(),
        lifespan=lifespan,
    )
    app.state.limiter = limiter

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html(username: str = Depends(get_current_username)):
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=app.title + " - Swagger UI",
            swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
            swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
        )

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html(username: str = Depends(get_current_username)):
        return get_redoc_html(openapi_url="/openapi.json", title=app.title + " - ReDoc")

    @app.get("/openapi.json", include_in_schema=False)
    async def get_open_api_endpoint(username: str = Depends(get_current_username)):
        return get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

    register_exceptions(app)
    register_routers(app, prefix="/api")

    if settings.DEBUG and settings.APP_ENV == "development":
        # Local-only debug helpers. Never expose token minting in shared environments.
        @app.get("/debug/token", include_in_schema=False)
        async def debug_token(username: str = "admin"):
            from core.security import create_access_token

            token = create_access_token(user_id=1, username=username, is_superuser=True)
            return {"token": token, "username": username}

        import os

        static_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "static")
        )
        os.makedirs(static_dir, exist_ok=True)
        app.mount("/debug", StaticFiles(directory=static_dir, html=True))

    return app


app = create_app()
