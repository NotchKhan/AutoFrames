from __future__ import annotations

import errno
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


@dataclass(slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def filesystem_error(exc: OSError) -> ApiError:
    if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == 112:
        return ApiError(507, "insufficient_storage", "На сервере недостаточно свободного места.")
    if isinstance(exc, PermissionError):
        return ApiError(500, "storage_permission_denied", "Сервер не может записать файл проекта.")
    return ApiError(500, "storage_error", "Ошибка файлового хранилища проекта.")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [".".join(str(part) for part in error["loc"] if part != "body") for error in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_error",
                    "message": "Параметры запроса не прошли проверку.",
                    "details": {"fields": fields},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        messages = {
            404: "Маршрут API не найден.",
            405: "Метод запроса не поддерживается.",
            413: "Размер запроса превышает допустимый лимит.",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"http_{exc.status_code}",
                    "message": messages.get(exc.status_code, "HTTP-запрос не может быть выполнен."),
                    "details": {},
                }
            },
            headers=exc.headers,
        )
