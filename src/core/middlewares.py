import json
import re
from datetime import datetime
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from core.security import verify_token
from db.session import AsyncSessionLocal
from log import logger
from models import AuditLog, User

SENSITIVE_KEYS = {"password", "token", "authorization", "access_token", "refresh_token"}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in SENSITIVE_KEYS else _sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


async def get_user_from_token_for_middleware(token: str, db) -> User | None:
    try:
        payload = verify_token(token, token_type="access")
        return await db.get(User, int(payload["user_id"]))
    except Exception:
        return None


class HttpAuditLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, methods: list[str] | None = None, exclude_paths: list[str] | None = None):
        super().__init__(app)
        self.methods = methods or ["POST", "PUT", "DELETE"]
        self.exclude_paths = exclude_paths or []

    async def get_request_args(self, request: Request) -> str:
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if not body:
                return ""
            try:
                return json.dumps(_sanitize(json.loads(body.decode("utf-8"))), ensure_ascii=False)
            except Exception:
                return "<binary>"
        return json.dumps(dict(request.query_params), ensure_ascii=False)

    async def get_response_body(self, response: Response) -> str:
        body = getattr(response, "body", b"")
        if not body:
            return ""
        try:
            parsed = json.loads(body.decode("utf-8"))
            return json.dumps(_sanitize(parsed), ensure_ascii=False)
        except Exception:
            return ""

    async def get_request_log(self, request: Request, response: Response) -> dict:
        route = request.scope.get("route")
        data = {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ip": request.client.host if request.client else "",
            "user_agent": request.headers.get("user-agent", ""),
            "module": "",
            "summary": "",
            "user_id": 0,
            "username": "",
        }
        if route is not None:
            data["module"] = ",".join(getattr(route, "tags", []) or [])
            data["summary"] = getattr(route, "summary", "") or ""

        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()
            async with AsyncSessionLocal() as db:
                user = await get_user_from_token_for_middleware(token, db)
                if user:
                    data["user_id"] = user.id
                    data["username"] = user.username
        return data

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = datetime.now()
        request_args = await self.get_request_args(request)
        response = await call_next(request)
        process_time = int((datetime.now().timestamp() - start_time.timestamp()) * 1000)

        if request.method in self.methods and not any(re.search(path, request.url.path, re.I) for path in self.exclude_paths):
            try:
                data = await self.get_request_log(request, response)
                data["response_time"] = process_time
                data["request_args"] = request_args
                data["response_body"] = await self.get_response_body(response)
                async with AsyncSessionLocal() as db:
                    db.add(AuditLog(**data))
                    await db.commit()
            except Exception:
                logger.exception("Failed to write audit log")
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = datetime.now()
        logger.info(f"请求开始: {request.method} {request.url.path}")
        try:
            response = await call_next(request)
            process_time = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(f"请求完成: {request.method} {request.url.path} - {response.status_code} ({process_time:.2f}ms)")
            return response
        except Exception as exc:
            process_time = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"请求异常: {request.method} {request.url.path} - {exc} ({process_time:.2f}ms)")
            raise
