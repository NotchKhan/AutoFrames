from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from api.errors import ApiError, filesystem_error
from config import (
    AUDIO_ANALYSIS_CONCURRENCY,
    AUDIO_MINIMUM_SCENE_MS,
    AUDIO_MINIMUM_SILENCE_MS,
    AUDIO_SILENCE_NOISE_DB,
    AUDIO_EXTENSIONS,
    AUDIO_MIME_TYPES,
    IMAGE_EXTENSIONS,
    IMAGE_MIME_TYPES,
    MAX_AUDIO_FILE_BYTES,
    MAX_AUDIO_TRACKS,
    MAX_IMAGE_FILES,
    MAX_IMAGE_FILE_BYTES,
    MAX_TOTAL_FILE_BYTES,
    OPENAI_API_BASE_URL,
    OPENAI_API_KEY,
    OPENAI_TRANSCRIPTION_ENABLED,
    OPENAI_TRANSCRIPTION_LANGUAGE,
    OPENAI_TRANSCRIPTION_MAX_MINUTES_PER_HOUR,
    OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS,
    PROJECT_TTL_HOURS,
    RENDER_CONCURRENCY,
)
from models.api import (
    ProgressResponse,
    ProjectResponse,
    RenderRequest,
    StatusResponse,
    TimelineResponse,
    TimelineRowResponse,
    UploadResponse,
    ValidationIssueResponse,
)
from models.render import AudioSettings, RenderResult, RenderSettings, VideoSettings
from models.timeline import SourceImage, TimelineItem, ValidationIssue
from services.audio_analyzer import (
    AudioAnalysisError,
    AudioConcatError,
    AudioPause,
    concatenate_audio_tracks,
    detect_audio_pauses,
    prepare_transcription_audio,
)
from services.image_processor import ImageValidationError, validate_image
from services.media_probe import (
    MediaProbeError,
    check_media_tools,
    probe_audio_duration_ms,
)
from services.settings_validator import validate_render_settings
from services.scene_sync import SyncStrategy
from services.speech_recognizer import (
    SpeechRecognitionError,
    SpeechTranscript,
    transcribe_audio,
)
from services.timeline_builder import build_audio_timeline
from services.timeline_validator import validate_timeline, validate_timeline_for_fps
from services.video_renderer import VideoRenderer
from services.workspace_manager import WorkspaceManager
from utils.time_utils import format_ms


LOGGER = logging.getLogger(__name__)
_PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_BATCH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_ACTIVE_STATUSES = {"queued", "rendering", "cancelling"}

ImageBatchSignature = tuple[tuple[str, int | None, str], ...]


@dataclass(slots=True)
class ProjectRecord:
    project_id: str
    workspace: WorkspaceManager
    created_at: datetime
    touched_at: datetime
    status: str = "draft"
    images: list[SourceImage] = field(default_factory=list)
    audio_path: Path | None = None
    audio_original_filenames: list[str] = field(default_factory=list)
    audio_duration_ms: int | None = None
    audio_pauses: list[AudioPause] | None = None
    speech_transcript: SpeechTranscript | None = None
    audio_analysis_complete: bool = False
    audio_analysis_warning: str | None = None
    sync_strategy: SyncStrategy = "adaptive"
    timeline: list[TimelineItem] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    render_thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    stage: str = "Ожидание файлов"
    current: int = 0
    total: int = 0
    completed_operations: int = 0
    progress_percent: float = 0.0
    message: str = "Загрузите изображения и аудио."
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=30))
    error: str | None = None
    result_path: Path | None = None
    media_info: dict[str, Any] = field(default_factory=dict)
    last_render_request_id: str | None = None
    image_batch_receipts: dict[str, ImageBatchSignature] = field(default_factory=dict)
    mutation_in_progress: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def expires_at(self) -> datetime:
        return self.touched_at + timedelta(hours=PROJECT_TTL_HOURS)


class ProjectService:
    def __init__(self) -> None:
        self._records: dict[str, ProjectRecord] = {}
        self._expired_ids: set[str] = set()
        self._lock = threading.RLock()
        self._transcription_usage: deque[tuple[float, int]] = deque()
        self._transcription_usage_lock = threading.Lock()
        self._transcription_slots = asyncio.Semaphore(2)
        self._audio_analysis_slots = asyncio.Semaphore(AUDIO_ANALYSIS_CONCURRENCY)
        self._analysis_ffmpeg_slots = asyncio.Semaphore(AUDIO_ANALYSIS_CONCURRENCY)
        self._render_slots = threading.Semaphore(RENDER_CONCURRENCY)
        self.ffmpeg_path, self.ffprobe_path, self.media_tool_errors = check_media_tools()

    def create_project(self) -> ProjectResponse:
        self.cleanup_expired()
        workspace = WorkspaceManager()
        now = datetime.now(UTC)
        record = ProjectRecord(workspace.project_id, workspace, now, now)
        with self._lock:
            self._records[record.project_id] = record
        return ProjectResponse(
            project_id=record.project_id,
            status="draft",
            expires_at=record.expires_at.isoformat(),
        )

    def _get(self, project_id: str, *, touch: bool = True) -> ProjectRecord:
        if not _PROJECT_ID_RE.fullmatch(project_id):
            raise ApiError(404, "project_not_found", "Проект не найден.")
        with self._lock:
            if project_id in self._expired_ids:
                raise ApiError(410, "project_expired", "Срок хранения проекта истёк. Создайте новый проект.")
            record = self._records.get(project_id)
        if record is None:
            raise ApiError(404, "project_not_found", "Проект не найден.")
        now = datetime.now(UTC)
        with record.lock:
            if record.status not in _ACTIVE_STATUSES and now >= record.expires_at:
                self._expire(record)
                raise ApiError(410, "project_expired", "Срок хранения проекта истёк. Создайте новый проект.")
            if touch:
                record.touched_at = now
        return record

    @staticmethod
    def _external_filename(upload: UploadFile) -> str:
        raw = (upload.filename or "").strip()
        if not raw or raw in {".", ".."} or "\x00" in raw:
            raise ApiError(422, "invalid_filename", "Загруженный файл не имеет корректного имени.")
        if "/" in raw or "\\" in raw or Path(raw).name != raw:
            raise ApiError(422, "unsafe_filename", "Имя файла содержит путь. Передайте только имя файла.")
        if len(raw) > 512:
            raise ApiError(422, "filename_too_long", "Имя файла длиннее 512 символов.")
        return raw

    @staticmethod
    def _mime(upload: UploadFile) -> str:
        return (upload.content_type or "").split(";", 1)[0].strip().lower()

    @staticmethod
    def _image_batch_id(batch_id: str | None) -> str | None:
        if batch_id is None:
            return None
        if not _BATCH_ID_RE.fullmatch(batch_id):
            raise ApiError(
                422,
                "invalid_batch_id",
                "batch_id должен содержать от 8 до 128 символов: латинские буквы, цифры, точку, дефис, подчёркивание или двоеточие.",
            )
        return batch_id

    @classmethod
    def _image_batch_signature(cls, uploads: list[UploadFile]) -> ImageBatchSignature:
        return tuple(
            (
                cls._external_filename(upload),
                upload.size,
                cls._mime(upload),
            )
            for upload in uploads
        )

    @staticmethod
    def _project_size(record: ProjectRecord) -> int:
        paths = [source.stored_path for source in record.images]
        if record.audio_path is not None:
            paths.append(record.audio_path)
        return sum(path.stat().st_size for path in paths if path.is_file())

    @staticmethod
    async def _stream_upload(
        upload: UploadFile,
        destination: Path,
        *,
        file_limit: int,
        total_before: int,
    ) -> int:
        size = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as target:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > file_limit:
                        raise ApiError(413, "file_too_large", "Размер одного файла превышает установленный лимит.")
                    if total_before + size > MAX_TOTAL_FILE_BYTES:
                        raise ApiError(413, "project_too_large", "Общий размер файлов проекта превышает установленный лимит.")
                    target.write(chunk)
        except ApiError:
            destination.unlink(missing_ok=True)
            raise
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise filesystem_error(exc) from exc
        except BaseException:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Не удалось очистить незавершённую загрузку %s", destination.name)
            raise
        if size == 0:
            destination.unlink(missing_ok=True)
            raise ApiError(422, "empty_file", "Загруженный файл пуст.")
        return size

    @staticmethod
    def _ensure_editable(record: ProjectRecord) -> None:
        if record.status in _ACTIVE_STATUSES:
            raise ApiError(409, "render_in_progress", "Нельзя изменять файлы во время рендеринга.")
        if record.mutation_in_progress:
            raise ApiError(409, "project_busy", "Для проекта уже выполняется загрузка или изменение файлов.")

    @staticmethod
    def _discard_result(record: ProjectRecord) -> None:
        if record.result_path is not None:
            try:
                record.result_path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Не удалось удалить прежний результат проекта %s", record.project_id)
        record.result_path = None
        record.media_info = {}
        record.error = None
        record.last_render_request_id = None
        record.render_thread = None
        record.status = "draft"
        record.progress_percent = 0.0
        record.completed_operations = 0

    def _rebuild_timeline(self, record: ProjectRecord) -> None:
        if record.audio_duration_ms is not None:
            items, build_issues = build_audio_timeline(
                record.images,
                record.audio_duration_ms,
                record.audio_pauses or [],
                record.speech_transcript,
                preferred_minimum_scene_ms=AUDIO_MINIMUM_SCENE_MS,
                strategy=record.sync_strategy,
            )
        else:
            items, build_issues = [], []
        validation_issues = validate_timeline(items) if items else []
        seen: set[tuple[str, str | None]] = set()
        issues: list[ValidationIssue] = []
        for issue in [*build_issues, *validation_issues]:
            key = (issue.message, issue.filename)
            if key not in seen:
                issues.append(issue)
                seen.add(key)
        record.timeline = items
        record.issues = issues
        if record.status not in _ACTIVE_STATUSES and record.status != "completed":
            if not issues and items and record.audio_path is not None and record.audio_duration_ms:
                record.status = "ready"
                record.stage = "Готово к рендерингу"
                record.message = "Таймлайн проверен. Можно запускать рендеринг."
            else:
                record.status = "draft"
                record.stage = "Проверка проекта"
                record.message = "Нужно исправить ошибки или загрузить недостающие файлы."

    def _reserve_transcription_budget(self, audio_duration_ms: int) -> bool:
        now = time.monotonic()
        limit_ms = round(OPENAI_TRANSCRIPTION_MAX_MINUTES_PER_HOUR * 60_000)
        with self._transcription_usage_lock:
            while self._transcription_usage and now - self._transcription_usage[0][0] >= 3600:
                self._transcription_usage.popleft()
            used_ms = sum(duration for _, duration in self._transcription_usage)
            if used_ms + audio_duration_ms > limit_ms:
                return False
            self._transcription_usage.append((now, audio_duration_ms))
            return True

    async def _recognize_speech(
        self,
        record: ProjectRecord,
        audio_path: Path,
        audio_duration_ms: int,
    ) -> SpeechTranscript:
        if self.ffmpeg_path is None:
            raise SpeechRecognitionError("FFmpeg недоступен для подготовки дорожки.")
        prepared = record.workspace.prepared_dir / f"transcription_{uuid.uuid4().hex}.mp3"
        try:
            try:
                async with self._analysis_ffmpeg_slots:
                    await asyncio.to_thread(
                        prepare_transcription_audio,
                        audio_path,
                        self.ffmpeg_path,
                        prepared,
                        audio_duration_ms,
                    )
                    prepared_size = prepared.stat().st_size
            except (AudioAnalysisError, OSError) as exc:
                raise SpeechRecognitionError(
                    "Не удалось подготовить дорожку для распознавания."
                ) from exc
            if prepared_size > 24 * 1024 * 1024:
                raise SpeechRecognitionError(
                    "Подготовленная дорожка слишком велика для точного распознавания."
                )
            async with self._transcription_slots:
                return await transcribe_audio(
                    prepared,
                    audio_duration_ms,
                    api_key=OPENAI_API_KEY or "",
                    base_url=OPENAI_API_BASE_URL,
                    language=OPENAI_TRANSCRIPTION_LANGUAGE,
                    timeout_seconds=OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS,
                )
        finally:
            try:
                prepared.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning(
                    "Не удалось удалить временную дорожку распознавания проекта %s",
                    record.project_id,
                )

    async def _detect_pauses(
        self,
        audio_path: Path,
        audio_duration_ms: int,
    ) -> list[AudioPause]:
        async with self._analysis_ffmpeg_slots:
            return await asyncio.to_thread(
                detect_audio_pauses,
                audio_path,
                self.ffmpeg_path,
                audio_duration_ms,
                noise_db=AUDIO_SILENCE_NOISE_DB,
                minimum_silence_ms=AUDIO_MINIMUM_SILENCE_MS,
            )

    async def _analyze_audio_for(
        self,
        record: ProjectRecord,
        audio_path: Path | None,
        audio_duration_ms: int | None,
    ) -> tuple[list[AudioPause], SpeechTranscript | None, str | None]:
        if audio_path is None or audio_duration_ms is None:
            return [], None, None
        if self.ffmpeg_path is None:
            return [], None, (
                "FFmpeg недоступен: кадры распределены равномерно без анализа пауз."
            )

        async with self._audio_analysis_slots:
            return await self._analyze_audio_unlocked(
                record,
                audio_path,
                audio_duration_ms,
            )

    async def _analyze_audio_unlocked(
        self,
        record: ProjectRecord,
        audio_path: Path,
        audio_duration_ms: int,
    ) -> tuple[list[AudioPause], SpeechTranscript | None, str | None]:

        pause_task = self._detect_pauses(audio_path, audio_duration_ms)
        speech_task = None
        speech_skip_warning: str | None = None
        if not OPENAI_TRANSCRIPTION_ENABLED:
            speech_skip_warning = (
                "Распознавание фраз отключено; использованы локальные паузы. "
                "Проверка границ относительно отдельных слов недоступна."
            )
        elif not OPENAI_API_KEY:
            speech_skip_warning = (
                "Серверный ключ распознавания не настроен; использованы локальные паузы. "
                "Проверка границ относительно отдельных слов недоступна."
            )
        elif not self._reserve_transcription_budget(audio_duration_ms):
            speech_skip_warning = (
                "Часовой лимит распознавания исчерпан; использованы локальные паузы. "
                "Проверка границ относительно отдельных слов недоступна."
            )
        else:
            speech_task = self._recognize_speech(record, audio_path, audio_duration_ms)

        if speech_task is None:
            try:
                pauses = await pause_task
            except AudioAnalysisError as exc:
                LOGGER.warning("Не удалось проанализировать паузы в аудиодорожке: %s", exc)
                return [], None, (
                    "Паузы не удалось определить; кадры распределены равномерно."
                )
            return pauses, None, speech_skip_warning

        pause_result, speech_result = await asyncio.gather(
            pause_task,
            speech_task,
            return_exceptions=True,
        )
        warnings: list[str] = []
        if isinstance(pause_result, BaseException):
            if not isinstance(pause_result, AudioAnalysisError):
                raise pause_result
            LOGGER.warning("Локальный анализ пауз завершился ошибкой: %s", pause_result)
            pauses = []
            warnings.append("Локальные паузы не определены.")
        else:
            pauses = pause_result
        if isinstance(speech_result, BaseException):
            if not isinstance(speech_result, SpeechRecognitionError):
                raise speech_result
            LOGGER.warning("Распознавание фраз завершилось ошибкой: %s", speech_result)
            transcript = None
            if pauses:
                warnings.append(
                    "Распознавание фраз недоступно; использован локальный анализ пауз. "
                    "Проверка границ относительно отдельных слов недоступна."
                )
            else:
                warnings.append(
                    "Распознавание фраз недоступно; кадры распределены равномерно."
                )
        else:
            transcript = speech_result
        return pauses, transcript, " ".join(warnings) or None

    async def upload_images(
        self,
        project_id: str,
        uploads: list[UploadFile],
        *,
        batch_id: str | None = None,
    ) -> UploadResponse:
        record = self._get(project_id)
        if not uploads:
            raise ApiError(422, "images_missing", "Не выбрано ни одного изображения.")
        normalized_batch_id = self._image_batch_id(batch_id)
        batch_signature = (
            self._image_batch_signature(uploads)
            if normalized_batch_id is not None
            else None
        )
        with record.lock:
            self._ensure_editable(record)
            if normalized_batch_id is not None:
                completed_signature = record.image_batch_receipts.get(normalized_batch_id)
                if completed_signature is not None:
                    if completed_signature != batch_signature:
                        raise ApiError(
                            409,
                            "batch_id_conflict",
                            "Этот batch_id уже использован для другой партии изображений.",
                        )
                    return UploadResponse(
                        project_id=project_id,
                        uploaded_count=0,
                        total_images=len(record.images),
                        audio_uploaded=record.audio_path is not None,
                        status=record.status,
                    )
            if len(record.images) + len(uploads) > MAX_IMAGE_FILES:
                raise ApiError(
                    413,
                    "too_many_images",
                    f"В одном проекте допускается не более {MAX_IMAGE_FILES} изображений.",
                )
            total_size = self._project_size(record)
            record.mutation_in_progress = True

        staged: list[SourceImage] = []
        try:
            for upload in uploads:
                original_name = self._external_filename(upload)
                extension = Path(original_name).suffix.lower()
                if extension not in IMAGE_EXTENSIONS:
                    raise ApiError(415, "unsupported_image_type", f"Файл «{original_name}» имеет неподдерживаемое расширение.")
                if self._mime(upload) not in IMAGE_MIME_TYPES:
                    raise ApiError(415, "invalid_image_mime", f"Файл «{original_name}» имеет недопустимый MIME-тип.")
                stored = record.workspace.uploads_dir / f"image_{uuid.uuid4().hex}{extension}"
                size = await self._stream_upload(
                    upload,
                    stored,
                    file_limit=MAX_IMAGE_FILE_BYTES,
                    total_before=total_size,
                )
                total_size += size
                try:
                    validate_image(stored, original_name)
                except ImageValidationError as exc:
                    stored.unlink(missing_ok=True)
                    raise ApiError(422, "corrupted_image", str(exc)) from exc
                staged.append(SourceImage(original_name, stored))
            analysis_result: tuple[list[AudioPause], SpeechTranscript | None, str | None] | None = None
            if (
                record.audio_path is not None
                and not record.audio_analysis_complete
            ):
                analysis_result = await self._analyze_audio_for(
                    record,
                    record.audio_path,
                    record.audio_duration_ms,
                )
            with record.lock:
                record.images.extend(staged)
                if analysis_result is not None:
                    pauses, transcript, analysis_warning = analysis_result
                    record.audio_pauses = pauses
                    record.speech_transcript = transcript
                    record.audio_analysis_warning = analysis_warning
                    record.audio_analysis_complete = True
                self._discard_result(record)
                self._rebuild_timeline(record)
                if normalized_batch_id is not None and batch_signature is not None:
                    record.image_batch_receipts[normalized_batch_id] = batch_signature
                return UploadResponse(
                    project_id=project_id,
                    uploaded_count=len(staged),
                    total_images=len(record.images),
                    audio_uploaded=record.audio_path is not None,
                    status=record.status,
                )
        except BaseException:
            for source in staged:
                try:
                    source.stored_path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Не удалось очистить незавершённую загрузку %s",
                        source.stored_path.name,
                    )
            raise
        finally:
            with record.lock:
                record.mutation_in_progress = False

    async def upload_audio(
        self,
        project_id: str,
        uploads: list[UploadFile],
    ) -> UploadResponse:
        record = self._get(project_id)
        if not uploads:
            raise ApiError(422, "audio_missing", "Не выбрано ни одной аудиодорожки.")
        if len(uploads) > MAX_AUDIO_TRACKS:
            raise ApiError(
                413,
                "too_many_audio_tracks",
                f"За один раз можно добавить не более {MAX_AUDIO_TRACKS} аудиодорожек.",
            )
        if self.ffprobe_path is None:
            raise ApiError(503, "ffprobe_unavailable", "ffprobe не найден на сервере. Загрузка аудио не может быть проверена.")
        if len(uploads) > 1 and self.ffmpeg_path is None:
            raise ApiError(
                503,
                "ffmpeg_unavailable",
                "FFmpeg не найден на сервере. Несколько аудиодорожек нельзя объединить.",
            )
        validated: list[tuple[UploadFile, str, str]] = []
        for upload in uploads:
            original_name = self._external_filename(upload)
            extension = Path(original_name).suffix.lower()
            if extension not in AUDIO_EXTENSIONS:
                raise ApiError(415, "unsupported_audio_type", f"Файл «{original_name}» имеет неподдерживаемое расширение.")
            if self._mime(upload) not in AUDIO_MIME_TYPES:
                raise ApiError(415, "invalid_audio_mime", f"Файл «{original_name}» имеет недопустимый MIME-тип.")
            validated.append((upload, original_name, extension))
        with record.lock:
            self._ensure_editable(record)
            total_size = self._project_size(record)
            old_size = record.audio_path.stat().st_size if record.audio_path and record.audio_path.is_file() else 0
            record.mutation_in_progress = True
        staged_paths: list[Path] = []
        combined_path: Path | None = None
        committed = False
        try:
            track_durations_ms: list[int] = []
            bytes_before = total_size - old_size
            for upload, original_name, extension in validated:
                stored = record.workspace.uploads_dir / f"audio_{uuid.uuid4().hex}{extension}"
                await self._stream_upload(
                    upload,
                    stored,
                    file_limit=MAX_AUDIO_FILE_BYTES,
                    total_before=bytes_before,
                )
                staged_paths.append(stored)
                bytes_before += stored.stat().st_size
                try:
                    track_durations_ms.append(
                        probe_audio_duration_ms(stored, self.ffprobe_path)
                    )
                except MediaProbeError as exc:
                    raise ApiError(
                        422,
                        "corrupted_audio",
                        f"Дорожка «{original_name}» повреждена или не содержит аудиопоток: {exc}",
                    ) from exc

            expected_duration_ms = sum(track_durations_ms)
            if len(staged_paths) == 1:
                effective_audio_path = staged_paths[0]
                duration_ms = track_durations_ms[0]
            else:
                combined_path = (
                    record.workspace.uploads_dir
                    / f"audio_combined_{uuid.uuid4().hex}.m4a"
                )
                try:
                    async with self._analysis_ffmpeg_slots:
                        await asyncio.to_thread(
                            concatenate_audio_tracks,
                            staged_paths,
                            self.ffmpeg_path,
                            combined_path,
                            expected_duration_ms,
                        )
                    duration_ms = probe_audio_duration_ms(
                        combined_path,
                        self.ffprobe_path,
                    )
                except (AudioConcatError, MediaProbeError) as exc:
                    raise ApiError(
                        422,
                        "audio_concat_failed",
                        "Не удалось последовательно объединить аудиодорожки. "
                        "Проверьте файлы и попробуйте снова.",
                    ) from exc
                allowed_difference_ms = max(250, len(staged_paths) * 60)
                if abs(duration_ms - expected_duration_ms) > allowed_difference_ms:
                    raise ApiError(
                        422,
                        "audio_concat_duration_mismatch",
                        "После склейки изменилась общая длительность аудио. "
                        "Операция отменена для сохранения синхронизации.",
                    )
                if total_size - old_size + combined_path.stat().st_size > MAX_TOTAL_FILE_BYTES:
                    raise ApiError(
                        413,
                        "project_too_large",
                        "Объединённая аудиодорожка превышает общий лимит проекта.",
                    )
                effective_audio_path = combined_path

            if record.images:
                pauses, transcript, analysis_warning = await self._analyze_audio_for(
                    record,
                    effective_audio_path,
                    duration_ms,
                )
                analysis_complete = True
            else:
                pauses, transcript, analysis_warning = [], None, None
                analysis_complete = False

            with record.lock:
                old_path = record.audio_path
                record.audio_path = effective_audio_path
                record.audio_original_filenames = [name for _upload, name, _extension in validated]
                record.audio_duration_ms = duration_ms
                record.audio_pauses = pauses
                record.speech_transcript = transcript
                record.audio_analysis_complete = analysis_complete
                record.audio_analysis_warning = analysis_warning
                self._discard_result(record)
                self._rebuild_timeline(record)
                response = UploadResponse(
                    project_id=project_id,
                    uploaded_count=len(validated),
                    total_images=len(record.images),
                    audio_uploaded=True,
                    status=record.status,
                )
                committed = True
            obsolete_paths = [
                path
                for path in [old_path, *staged_paths]
                if path is not None and path != effective_audio_path
            ]
            for obsolete_path in obsolete_paths:
                try:
                    obsolete_path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Не удалось удалить служебную аудиодорожку проекта %s",
                        project_id,
                    )
            return response
        except BaseException:
            if not committed:
                for staged_path in [*staged_paths, combined_path]:
                    if staged_path is None:
                        continue
                    try:
                        staged_path.unlink(missing_ok=True)
                    except OSError:
                        LOGGER.warning(
                            "Не удалось очистить незавершённую загрузку %s",
                            staged_path.name,
                        )
            raise
        finally:
            with record.lock:
                record.mutation_in_progress = False

    def set_sync_strategy(
        self,
        project_id: str,
        strategy: SyncStrategy,
    ) -> TimelineResponse:
        record = self._get(project_id)
        with record.lock:
            self._ensure_editable(record)
            if not record.images:
                raise ApiError(422, "images_missing", "Добавьте хотя бы одно изображение.")
            if record.audio_path is None or record.audio_duration_ms is None:
                raise ApiError(422, "audio_missing", "Добавьте корректную озвучку.")
            if record.sync_strategy != strategy:
                self._discard_result(record)
                record.sync_strategy = strategy
                self._rebuild_timeline(record)
        return self.timeline_response(project_id)

    def delete_image(self, project_id: str, image_id: str) -> UploadResponse:
        record = self._get(project_id)
        with record.lock:
            self._ensure_editable(record)
            source = next((item for item in record.images if item.stored_path.name == image_id), None)
            if source is None:
                raise ApiError(404, "image_not_found", "Изображение не найдено в проекте.")
            try:
                source.stored_path.unlink(missing_ok=True)
            except OSError as exc:
                raise filesystem_error(exc) from exc
            record.images.remove(source)
            self._discard_result(record)
            self._rebuild_timeline(record)
            return UploadResponse(
                project_id=project_id,
                uploaded_count=0,
                total_images=len(record.images),
                audio_uploaded=record.audio_path is not None,
                status=record.status,
            )

    def timeline_response(self, project_id: str) -> TimelineResponse:
        record = self._get(project_id)
        with record.lock:
            if record.mutation_in_progress:
                raise ApiError(409, "project_busy", "Дождитесь завершения загрузки файлов.")
            issues = list(record.issues)
            if record.audio_path is None:
                issues.append(ValidationIssue("Аудиофайл не загружен."))
            items = [
                TimelineRowResponse(
                    index=item.index,
                    image_id=item.stored_path.name,
                    original_filename=item.original_filename,
                    parsed_timestamp=item.parsed_timestamp,
                    start_ms=item.start_ms,
                    end_ms=item.end_ms,
                    duration_ms=item.duration_ms,
                    start_formatted=item.start_formatted,
                    end_formatted=item.end_formatted,
                    duration_formatted=item.duration_formatted,
                    is_valid=item.is_valid,
                    errors=list(item.errors),
                    warnings=list(item.warnings),
                    boundary_kind=item.boundary_kind,
                )
                for item in record.timeline
            ]
            timeline_end = items[-1].end_ms if items else None
            audio_duration = record.audio_duration_ms
            difference = audio_duration - timeline_end if audio_duration is not None and timeline_end is not None else None
            timeline_mode = "audio_pauses"
            boundary_kinds = {item.boundary_kind for item in record.timeline[:-1]}
            if not record.timeline and record.speech_transcript is not None:
                analysis_method = "unavailable"
            elif boundary_kinds & {
                "sentence_pause", "sentence_end", "segment_pause", "segment_end"
            }:
                analysis_method = "phrases_and_pauses"
            elif boundary_kinds & {"long_pause", "short_pause"}:
                analysis_method = "pauses"
            elif "word_boundary" in boundary_kinds:
                analysis_method = "word_boundaries"
            else:
                analysis_method = "even"
            analysis_warnings: list[str] = []
            if record.audio_analysis_warning:
                analysis_warnings.append(record.audio_analysis_warning)
            if record.speech_transcript is not None:
                sentence_count = record.speech_transcript.estimated_sentence_count
                internal_sentence_count = (
                    record.speech_transcript.internal_sentence_boundary_count
                )
                required_transitions = max(0, len(record.images) - 1)
                sentence_transitions = sum(
                    item.boundary_kind in {"sentence_pause", "sentence_end"}
                    for item in record.timeline[:-1]
                )
                if not record.timeline and required_transitions:
                    analysis_warnings.append(
                        f"Для {required_transitions} смен кадров не хватило безопасных "
                        f"окончаний предложений и межсловных пауз "
                        f"(всего распознано предложений: {sentence_count}). "
                        "Таймлайн не построен, чтобы не разрезать речь."
                    )
                elif (
                    internal_sentence_count < required_transitions
                    or sentence_transitions < required_transitions
                ):
                    analysis_warnings.append(
                        f"Для {required_transitions} смен кадров удалось использовать "
                        f"{sentence_transitions} окончаний предложений "
                        f"(всего распознано: {sentence_count}). Остальные границы выбраны "
                        "по окончаниям фраз, паузам или безопасным промежуткам между словами."
                    )
                elif internal_sentence_count > required_transitions:
                    analysis_warnings.append(
                        f"Распознано предложений: {sentence_count}, кадров: {len(record.images)}. "
                        "Некоторые соседние предложения останутся внутри одного кадра."
                    )
            return TimelineResponse(
                project_id=project_id,
                timeline_mode=timeline_mode,
                sync_strategy=record.sync_strategy,
                detected_pauses=(len(record.audio_pauses or []) if timeline_mode == "audio_pauses" else 0),
                detected_sentences=(
                    record.speech_transcript.estimated_sentence_count
                    if timeline_mode == "audio_pauses" and record.speech_transcript is not None
                    else 0
                ),
                transcription_used=(
                    timeline_mode == "audio_pauses" and record.speech_transcript is not None
                ),
                analysis_method=analysis_method,
                analysis_warning=" ".join(analysis_warnings) or None,
                is_valid=not issues and bool(items) and audio_duration is not None,
                items=items,
                issues=[ValidationIssueResponse(message=issue.message, filename=issue.filename, critical=issue.critical) for issue in issues],
                audio_uploaded=record.audio_path is not None,
                audio_track_count=len(record.audio_original_filenames),
                audio_duration_ms=audio_duration,
                audio_duration_formatted=format_ms(audio_duration) if audio_duration is not None else None,
                timeline_end_ms=timeline_end,
                timeline_end_formatted=format_ms(timeline_end) if timeline_end is not None else None,
                difference_ms=difference,
            )

    @staticmethod
    def _render_settings(payload: RenderRequest) -> RenderSettings:
        video = VideoSettings(**payload.video.model_dump())
        audio = AudioSettings(**payload.audio.model_dump())
        return RenderSettings(
            video=video,
            audio=audio,
            end_mode=payload.end_mode,
            keep_debug_files=payload.keep_debug_files,
            preview_start_ms=payload.preview_start_ms,
            preview_end_ms=payload.preview_end_ms,
        )

    def start_render(self, project_id: str, payload: RenderRequest) -> str:
        record = self._get(project_id)
        with record.lock:
            if (
                payload.request_id is not None
                and payload.request_id == record.last_render_request_id
                and record.status in {*_ACTIVE_STATUSES, "cancelled", "completed", "failed"}
            ):
                return record.status
            if record.mutation_in_progress:
                raise ApiError(409, "project_busy", "Дождитесь завершения загрузки файлов.")
            if record.status in _ACTIVE_STATUSES:
                raise ApiError(409, "render_in_progress", "Рендеринг этого проекта уже выполняется.")
            if not record.images:
                raise ApiError(422, "images_missing", "Добавьте хотя бы одно изображение.")
            if record.audio_path is None or record.audio_duration_ms is None:
                raise ApiError(422, "audio_missing", "Добавьте корректный аудиофайл.")
            try:
                audio_available = record.audio_path.is_file() and record.audio_path.stat().st_size > 0
                missing_images = [
                    item.original_filename
                    for item in record.timeline
                    if not item.stored_path.is_file() or item.stored_path.stat().st_size <= 0
                ]
            except OSError as exc:
                raise filesystem_error(exc) from exc
            if not audio_available:
                raise ApiError(
                    422,
                    "audio_source_missing",
                    "Исходное аудио проекта недоступно. Загрузите аудиодорожки заново.",
                )
            if missing_images:
                raise ApiError(
                    422,
                    "image_source_missing",
                    "Часть исходных изображений недоступна. Загрузите кадры заново.",
                    {"filenames": missing_images[:10]},
                )
            if record.issues:
                raise ApiError(422, "timeline_invalid", "Таймлайн содержит критические ошибки.", {"issues": [issue.message for issue in record.issues]})
            if self.ffmpeg_path is None or self.ffprobe_path is None:
                raise ApiError(503, "media_tools_unavailable", "FFmpeg или ffprobe не найдены на сервере.")
            settings = self._render_settings(payload)
            setting_issues = [
                *validate_render_settings(settings),
                *validate_timeline_for_fps(record.timeline, settings.video.fps),
            ]
            if setting_issues:
                raise ApiError(422, "invalid_render_settings", setting_issues[0].message)
            self._discard_result(record)
            record.cancel_event = threading.Event()
            record.last_render_request_id = payload.request_id
            record.status = "queued"
            record.stage = "Очередь"
            record.message = "Задача принята и ожидает запуска."
            record.current = 0
            record.total = 0
            record.logs.clear()
            record.logs.append(record.message)
            record.render_thread = threading.Thread(
                target=self._render_worker,
                args=(record, settings),
                name=f"render-{record.project_id[:8]}",
                daemon=True,
            )
            try:
                record.render_thread.start()
            except Exception as exc:
                LOGGER.exception("Не удалось запустить поток рендера проекта %s", record.project_id)
                record.render_thread = None
                record.last_render_request_id = None
                record.status = "ready"
                record.stage = "Не удалось запустить рендеринг"
                record.message = "Сервер не смог запустить обработку. Попробуйте ещё раз."
                record.error = record.message
                record.logs.append(record.message)
                raise ApiError(503, "render_worker_unavailable", record.message) from exc
            return record.status

    @staticmethod
    def _stage_percent(stage: str, current: int, total: int) -> float:
        ranges = {
            "Проверка файлов": (1.0, 5.0),
            "Подготовка изображений": (5.0, 20.0),
            "Создание видеоклипов": (20.0, 75.0),
            "Объединение видеоклипов": (75.0, 84.0),
            "Добавление аудио": (84.0, 94.0),
            "Финальная проверка": (94.0, 99.0),
            "Готово": (100.0, 100.0),
        }
        start, end = ranges.get(stage, (0.0, 99.0))
        fraction = min(max(current / total, 0.0), 1.0) if total > 0 else 0.0
        return round(start + (end - start) * fraction, 1)

    def _render_worker(self, record: ProjectRecord, settings: RenderSettings) -> None:
        try:
            with self._render_slots:
                if record.cancel_event.is_set():
                    with record.lock:
                        record.touched_at = datetime.now(UTC)
                        record.status = "cancelled"
                        record.stage = "Отменено"
                        record.message = "Рендеринг отменён до запуска."
                        record.error = "Операция отменена пользователем."
                        record.logs.append(record.message)
                    return
                self._render_in_slot(record, settings)
        except Exception:
            LOGGER.exception("Рабочий поток рендера проекта %s аварийно завершился", record.project_id)
            with record.lock:
                if record.status in _ACTIVE_STATUSES:
                    record.touched_at = datetime.now(UTC)
                    record.status = "failed"
                    record.stage = "Ошибка"
                    record.message = "Рабочий процесс рендеринга аварийно завершился."
                    record.error = "Повторите сборку. Если ошибка сохранится, уменьшите разрешение или число кадров."
                    record.logs.append(record.message)
                    record.logs.append(f"Причина: {record.error}")

    def _render_in_slot(self, record: ProjectRecord, settings: RenderSettings) -> None:
        with record.lock:
            record.status = "rendering"
            record.stage = "Проверка файлов"
            record.message = "Рендеринг запущен."
            record.logs.append(record.message)

        def progress(stage: str, current: int, total: int, message: str) -> None:
            with record.lock:
                record.stage = stage
                record.current = current
                record.total = total
                record.completed_operations += 1
                record.progress_percent = self._stage_percent(stage, current, total)
                record.message = message
                record.logs.append(message)

        try:
            renderer = VideoRenderer(self.ffmpeg_path, self.ffprobe_path, record.workspace)  # type: ignore[arg-type]
            result = renderer.render(
                list(record.timeline),
                record.audio_path,  # type: ignore[arg-type]
                int(record.audio_duration_ms or 0),
                settings,
                progress=progress,
                cancel_event=record.cancel_event,
            )
        except Exception:
            LOGGER.exception("Непредвиденная ошибка рендера проекта %s", record.project_id)
            result = RenderResult(False, error="Внутренняя ошибка рендеринга. Подробности записаны в журнал сервера.")
        with record.lock:
            record.touched_at = datetime.now(UTC)
            record.media_info = dict(result.media_info)
            valid_output = False
            if result.success and result.output_path is not None:
                try:
                    record.workspace.require_owned_path(result.output_path, record.workspace.output_dir)
                    valid_output = result.output_path.is_file() and result.output_path.stat().st_size > 0
                except (OSError, ValueError):
                    LOGGER.exception("Рендер проекта %s вернул недоступный файл", record.project_id)
            if result.success and valid_output and result.output_path is not None:
                record.status = "completed"
                record.progress_percent = 100.0
                record.stage = "Готово"
                record.message = "Видео успешно собрано и проверено."
                record.result_path = result.output_path
                record.error = None
            elif result.cancelled:
                record.status = "cancelled"
                record.stage = "Отменено"
                record.message = "Рендеринг отменён."
                record.error = result.error
            else:
                record.status = "failed"
                record.stage = "Ошибка"
                record.message = "Рендеринг завершился с ошибкой."
                record.error = (
                    result.error
                    or (
                        "Рендеринг не создал доступный MP4-файл. Повторите сборку."
                        if result.success
                        else "Неизвестная ошибка рендеринга."
                    )
                )
            record.logs.append(record.message)
            if record.error:
                record.logs.append(f"Причина: {record.error}")
            for warning in result.warnings:
                record.logs.append(f"Предупреждение: {warning}")

    def progress_response(self, project_id: str) -> ProgressResponse:
        record = self._get(project_id)
        with record.lock:
            return ProgressResponse(
                project_id=project_id,
                status=record.status,
                stage=record.stage,
                progress_percent=record.progress_percent,
                current=record.current,
                total=record.total,
                completed_operations=record.completed_operations,
                message=record.message,
            )

    def status_response(self, project_id: str) -> StatusResponse:
        record = self._get(project_id)
        with record.lock:
            if (
                record.status in _ACTIVE_STATUSES
                and (record.render_thread is None or not record.render_thread.is_alive())
            ):
                record.status = "failed"
                record.stage = "Ошибка"
                record.message = "Рабочий процесс рендеринга неожиданно остановился."
                record.error = "Повторите сборку видео."
                record.logs.append(record.message)
                record.logs.append(f"Причина: {record.error}")
            result_ready = False
            if record.status == "completed" and record.result_path is not None:
                try:
                    result_ready = record.result_path.is_file() and record.result_path.stat().st_size > 0
                except OSError:
                    result_ready = False
            if record.status == "completed" and not result_ready:
                record.status = "failed"
                record.stage = "Ошибка"
                record.message = "Готовый MP4-файл оказался недоступен."
                record.error = "Запустите сборку ещё раз."
                record.logs.append(record.message)
                record.logs.append(f"Причина: {record.error}")
            return StatusResponse(
                project_id=project_id,
                status=record.status,
                stage=record.stage,
                progress_percent=record.progress_percent,
                current=record.current,
                total=record.total,
                completed_operations=record.completed_operations,
                message=record.message,
                recent_logs=list(record.logs),
                error=record.error,
                result_ready=result_ready,
                media_info=dict(record.media_info),
            )

    def cancel_render(self, project_id: str) -> str:
        record = self._get(project_id)
        with record.lock:
            if record.status not in {"queued", "rendering", "cancelling"}:
                raise ApiError(409, "render_not_running", "У проекта нет выполняющегося рендеринга.")
            record.cancel_event.set()
            record.status = "cancelling"
            record.stage = "Отмена"
            record.message = "Останавливаем FFmpeg и очищаем промежуточные файлы."
            record.logs.append(record.message)
            return record.status

    def result_path(self, project_id: str) -> Path:
        record = self._get(project_id)
        with record.lock:
            if record.status != "completed" or record.result_path is None:
                raise ApiError(409, "result_not_ready", "Готовый MP4 ещё недоступен.")
            if not record.result_path.is_file() or record.result_path.stat().st_size <= 0:
                raise ApiError(404, "result_missing", "Файл результата не найден. Запустите рендеринг снова.")
            return record.result_path

    def image_path(self, project_id: str, image_id: str) -> Path:
        record = self._get(project_id)
        with record.lock:
            source = next((item for item in record.images if item.stored_path.name == image_id), None)
            if source is None or not source.stored_path.is_file():
                raise ApiError(404, "image_not_found", "Изображение не найдено в проекте.")
            record.workspace.require_owned_path(source.stored_path, record.workspace.uploads_dir)
            return source.stored_path

    def delete_project(self, project_id: str) -> None:
        record = self._get(project_id, touch=False)
        thread: threading.Thread | None
        with record.lock:
            if record.mutation_in_progress:
                raise ApiError(409, "project_busy", "Дождитесь завершения загрузки файлов перед удалением проекта.")
            record.cancel_event.set()
            thread = record.render_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=8)
        if thread is not None and thread.is_alive():
            raise ApiError(409, "cancel_in_progress", "Рендеринг ещё останавливается. Повторите удаление через несколько секунд.")
        try:
            record.workspace.cleanup_project(keep_output=False, keep_logs=False)
        except OSError as exc:
            raise filesystem_error(exc) from exc
        with self._lock:
            self._records.pop(project_id, None)

    def _expire(self, record: ProjectRecord) -> None:
        with self._lock:
            self._records.pop(record.project_id, None)
            self._expired_ids.add(record.project_id)
            if len(self._expired_ids) > 10_000:
                self._expired_ids = set(list(self._expired_ids)[-5_000:])
        try:
            record.workspace.cleanup_project(keep_output=False, keep_logs=False)
        except OSError:
            LOGGER.exception("Не удалось очистить истёкший проект %s", record.project_id)

    def cleanup_expired(self) -> int:
        now = datetime.now(UTC)
        with self._lock:
            records = list(self._records.values())
        expired = 0
        for record in records:
            with record.lock:
                should_expire = record.status not in _ACTIVE_STATUSES and now >= record.expires_at
            if should_expire:
                self._expire(record)
                expired += 1
        WorkspaceManager.cleanup_stale(self._records.keys(), PROJECT_TTL_HOURS)
        return expired

    def shutdown(self) -> None:
        with self._lock:
            records = list(self._records.values())
        for record in records:
            with record.lock:
                if record.status in _ACTIVE_STATUSES:
                    record.cancel_event.set()
            thread = record.render_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=6)
