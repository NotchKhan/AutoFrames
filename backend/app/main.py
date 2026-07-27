from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.errors import install_error_handlers
from api.routes import router
from config import APP_TITLE, FRONTEND_ORIGINS, ensure_storage_directories
from models.api import HealthResponse
from services.project_service import ProjectService


def create_app(projects: ProjectService | None = None) -> FastAPI:
    service = projects or ProjectService()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        ensure_storage_directories()

        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(300)
                await asyncio.to_thread(application.state.projects.cleanup_expired)

        cleanup_task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup_task
            await asyncio.to_thread(application.state.projects.shutdown)

    application = FastAPI(
        title=APP_TITLE,
        description=(
            "API для автоматической синхронизации кадров по фразам и паузам в аудио, "
            "ручных временных меток и фонового рендеринга MP4 через FFmpeg."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.state.projects = service
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(FRONTEND_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )
    install_error_handlers(application)
    application.include_router(router)

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    def health(request: Request) -> HealthResponse:
        current: ProjectService = request.app.state.projects
        available = current.ffmpeg_path is not None and current.ffprobe_path is not None
        return HealthResponse(
            status="ok" if available else "degraded",
            service="autoframes-backend",
            ffmpeg=current.ffmpeg_path is not None,
            ffprobe=current.ffprobe_path is not None,
        )

    return application


app = create_app()
