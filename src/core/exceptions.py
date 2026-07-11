from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound, SQLAlchemyError
from starlette.responses import Response

from core.config import settings
from log import logger


class BusinessException(Exception):
    def __init__(self, message: str = "Business error", code: int = 400):
        self.message = message
        self.code = code


class NotFoundException(BusinessException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message=message, code=404)


class UnauthorizedException(BusinessException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message=message, code=401)


class ForbiddenException(BusinessException):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message=message, code=403)


async def BusinessExceptionHandle(_: Request, exc: BusinessException) -> JSONResponse:
    return JSONResponse(status_code=exc.code, content={"code": exc.code, "msg": exc.message, "data": None})


async def NotFoundHandle(_: Request, exc: NoResultFound) -> JSONResponse:
    msg = f"Object not found: {exc}" if settings.DEBUG else "请求的资源不存在"
    return JSONResponse(status_code=404, content={"code": 404, "msg": msg, "data": None})


async def HttpExcHandle(_: Request, exc: HTTPException):
    if exc.status_code == 401 and exc.headers and "WWW-Authenticate" in exc.headers:
        return Response(status_code=exc.status_code, headers=exc.headers)
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "msg": exc.detail, "data": None})


async def IntegrityHandle(_: Request, exc: IntegrityError):
    msg = f"IntegrityError: {exc}" if settings.DEBUG else "数据完整性错误，请检查输入数据"
    return JSONResponse(status_code=400, content={"code": 400, "msg": msg, "data": None})


async def SQLAlchemyHandle(_: Request, exc: SQLAlchemyError):
    msg = f"SQLAlchemyError: {exc}" if settings.DEBUG else "数据库错误"
    return JSONResponse(status_code=500, content={"code": 500, "msg": msg, "data": None})




async def GeneralExceptionHandle(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception")
    msg = f"Unhandled error: {exc}" if settings.DEBUG else "服务器内部错误"
    return JSONResponse(status_code=500, content={"code": 500, "msg": msg, "data": None})


async def RequestValidationHandle(_: Request, exc: RequestValidationError) -> JSONResponse:
    msg = exc.errors()[0]["msg"] if exc.errors() else "请求参数验证失败"
    return JSONResponse(status_code=422, content={"code": 422, "msg": msg, "data": None})


async def PydanticValidationHandle(_: Request, exc: ValidationError) -> JSONResponse:
    """捕获 Pydantic model 在校验器（field_validator）中抛出的错误。"""
    first = exc.errors()[0]
    msg = first["msg"].removeprefix("Value error, ")
    return JSONResponse(status_code=400, content={"code": 400, "msg": msg, "data": None})


async def ResponseValidationHandle(_: Request, exc: ResponseValidationError) -> JSONResponse:
    msg = f"ResponseValidationError: {exc}" if settings.DEBUG else "服务器响应格式错误"
    return JSONResponse(status_code=500, content={"code": 500, "msg": msg, "data": None})
