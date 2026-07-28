from __future__ import annotations

import mimetypes

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse

from models.api import (
    DeleteResponse,
    ProgressResponse,
    ProjectResponse,
    RenderAcceptedResponse,
    RenderRequest,
    StatusResponse,
    SyncStrategyRequest,
    TimelineResponse,
    UploadResponse,
)
from services.project_service import ProjectService


router = APIRouter(prefix="/api", tags=["projects"])


def _projects(request: Request) -> ProjectService:
    return request.app.state.projects


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(request: Request) -> ProjectResponse:
    """Создать изолированную рабочую область проекта."""
    return _projects(request).create_project()


@router.post("/projects/{project_id}/images", response_model=UploadResponse)
async def upload_images(
    project_id: str,
    request: Request,
    files: list[UploadFile] = File(..., description="Изображения по порядку сцен или с ручными метками времени"),
    batch_id: str | None = Form(
        None,
        description="Необязательный ключ идемпотентности одной завершённой партии изображений",
    ),
) -> UploadResponse:
    """Безопасно загрузить набор изображений и проверить их через Pillow."""
    return await _projects(request).upload_images(project_id, files, batch_id=batch_id)


@router.delete("/projects/{project_id}/images/{image_id}", response_model=UploadResponse)
def delete_image(project_id: str, image_id: str, request: Request) -> UploadResponse:
    """Удалить отдельное изображение до запуска рендеринга."""
    return _projects(request).delete_image(project_id, image_id)


@router.get("/projects/{project_id}/images/{image_id}", response_class=FileResponse)
def get_image(project_id: str, image_id: str, request: Request) -> FileResponse:
    """Получить изображение проекта для локального предпросмотра в таблице."""
    path = _projects(request).image_path(project_id, image_id)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})


@router.post("/projects/{project_id}/audio", response_model=UploadResponse)
async def upload_audio(
    project_id: str,
    request: Request,
    file: UploadFile | None = File(None, description="Одна дорожка (старый совместимый формат)"),
    files: list[UploadFile] | None = File(
        None,
        description="Несколько дорожек в порядке последовательного воспроизведения",
    ),
) -> UploadResponse:
    """Загрузить одну или несколько последовательных дорожек и проверить итоговое аудио."""
    ordered = ([file] if file is not None else []) + (files or [])
    return await _projects(request).upload_audio(project_id, ordered)


@router.get("/projects/{project_id}/timeline", response_model=TimelineResponse)
def validate_project_timeline(project_id: str, request: Request) -> TimelineResponse:
    """Синхронизировать сцены по аудио или ручным меткам и вернуть результат проверки."""
    return _projects(request).timeline_response(project_id)


@router.post("/projects/{project_id}/sync-strategy", response_model=TimelineResponse)
def change_sync_strategy(
    project_id: str,
    payload: SyncStrategyRequest,
    request: Request,
) -> TimelineResponse:
    """Перестроить автоматический таймлайн выбранным способом смены кадров."""
    return _projects(request).set_sync_strategy(project_id, payload.strategy)


@router.post(
    "/projects/{project_id}/render",
    response_model=RenderAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_render(project_id: str, payload: RenderRequest, request: Request) -> RenderAcceptedResponse:
    """Запустить FFmpeg-рендеринг в отдельном серверном потоке."""
    current_status = _projects(request).start_render(project_id, payload)
    return RenderAcceptedResponse(
        project_id=project_id,
        status=current_status,
        message="Запрос рендеринга принят. Состояние можно безопасно уточнять повторно.",
    )


@router.get("/projects/{project_id}/status", response_model=StatusResponse)
def get_status(project_id: str, request: Request) -> StatusResponse:
    """Получить состояние, прогресс, журнал и характеристики результата."""
    return _projects(request).status_response(project_id)


@router.get("/projects/{project_id}/progress", response_model=ProgressResponse)
def get_progress(project_id: str, request: Request) -> ProgressResponse:
    """Получить компактное состояние прогресса для частого polling."""
    return _projects(request).progress_response(project_id)


@router.post("/projects/{project_id}/cancel", response_model=RenderAcceptedResponse)
def cancel_render(project_id: str, request: Request) -> RenderAcceptedResponse:
    """Безопасно остановить активный FFmpeg-процесс и очистить промежуточные файлы."""
    current_status = _projects(request).cancel_render(project_id)
    return RenderAcceptedResponse(
        project_id=project_id,
        status=current_status,
        message="Запрошена отмена рендеринга.",
    )


@router.get("/projects/{project_id}/result", response_class=FileResponse)
def download_result(project_id: str, request: Request) -> FileResponse:
    """Скачать проверенный H.264/AAC MP4."""
    path = _projects(request).result_path(project_id)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename="final_video.mp4",
        headers={"Cache-Control": "private, no-store"},
    )


@router.delete("/projects/{project_id}", response_model=DeleteResponse)
def delete_project(project_id: str, request: Request) -> DeleteResponse:
    """Удалить проект, загрузки, логи и итоговый файл."""
    _projects(request).delete_project(project_id)
    return DeleteResponse(project_id=project_id, deleted=True)
