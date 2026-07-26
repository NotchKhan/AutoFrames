from __future__ import annotations

import shutil
import uuid
import re
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from config import (
    IMAGE_EXTENSIONS,
    LOG_ROOT,
    MAX_AUDIO_FILE_BYTES,
    MAX_IMAGE_FILE_BYTES,
    MAX_IMAGE_FILES,
    MAX_TOTAL_IMAGE_BYTES,
    OUTPUT_ROOT,
    TEMP_ROOT,
)
from models.timeline import SourceImage
from utils.file_utils import display_filename, human_file_size, unique_stored_name


_PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class WorkspaceLimitError(ValueError):
    """Размер или количество входных файлов превышает безопасный лимит."""


class UploadedFileLike(Protocol):
    name: str

    def getvalue(self) -> bytes: ...


def _uploaded_buffer(uploaded: UploadedFileLike) -> bytes | memoryview:
    getbuffer = getattr(uploaded, "getbuffer", None)
    return getbuffer() if callable(getbuffer) else uploaded.getvalue()


class WorkspaceManager:
    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id or uuid.uuid4().hex
        if not _PROJECT_ID_RE.fullmatch(self.project_id):
            raise ValueError("Некорректный ID проекта: ожидается внутренний 32-значный hex-ID.")
        self.root = TEMP_ROOT / self.project_id
        self.uploads_dir = self.root / "uploads"
        self.prepared_dir = self.root / "prepared"
        self.clips_dir = self.root / "clips"
        self.render_dir = self.root / "render"
        self.output_dir = OUTPUT_ROOT / self.project_id
        self.log_dir = LOG_ROOT / self.project_id
        for directory in (
            self.uploads_dir, self.prepared_dir, self.clips_dir,
            self.render_dir, self.output_dir, self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def save_uploaded_images(self, files: Sequence[UploadedFileLike]) -> list[SourceImage]:
        if len(files) > MAX_IMAGE_FILES:
            raise WorkspaceLimitError(
                f"Выбрано {len(files)} изображений. Максимум для одного проекта — {MAX_IMAGE_FILES}. "
                "Разделите материал на несколько проектов."
            )
        validated: list[tuple[str, bytes | memoryview]] = []
        total_size = 0
        for uploaded in files:
            original_name = display_filename(uploaded.name)
            data = _uploaded_buffer(uploaded)
            size = len(data)
            if size > MAX_IMAGE_FILE_BYTES:
                raise WorkspaceLimitError(
                    f"Изображение «{original_name}» занимает {human_file_size(size)}. "
                    f"Допустимый размер одного изображения — до {human_file_size(MAX_IMAGE_FILE_BYTES)}."
                )
            total_size += size
            if total_size > MAX_TOTAL_IMAGE_BYTES:
                raise WorkspaceLimitError(
                    f"Общий размер изображений превышает {human_file_size(MAX_TOTAL_IMAGE_BYTES)}. "
                    "Уменьшите файлы или разделите проект."
                )
            validated.append((original_name, data))
        sources: list[SourceImage] = []
        for position, (original_name, data) in enumerate(validated, start=1):
            stored = self.uploads_dir / unique_stored_name(position, original_name, data)
            if not stored.exists():
                with stored.open("wb") as destination:
                    destination.write(data)
            sources.append(SourceImage(original_name, stored))
        return sources

    def save_uploaded_audio(self, uploaded: UploadedFileLike) -> Path:
        original_name = display_filename(uploaded.name)
        data = _uploaded_buffer(uploaded)
        size = len(data)
        if size > MAX_AUDIO_FILE_BYTES:
            raise WorkspaceLimitError(
                f"Аудиофайл «{original_name}» занимает {human_file_size(size)}. "
                f"Допустимый размер — до {human_file_size(MAX_AUDIO_FILE_BYTES)}."
            )
        stored = self.uploads_dir / ("audio_" + unique_stored_name(0, original_name, data))
        if not stored.exists():
            with stored.open("wb") as destination:
                destination.write(data)
        return stored

    def import_folder(self, folder: Path) -> list[SourceImage]:
        resolved = folder.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("Выбранный путь не является папкой.")
        candidates = sorted(
            (path for path in resolved.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
            key=lambda path: path.name.casefold(),
        )
        if len(candidates) > MAX_IMAGE_FILES:
            raise WorkspaceLimitError(
                f"В папке найдено {len(candidates)} изображений. Максимум — {MAX_IMAGE_FILES}."
            )
        validated_sources: list[tuple[Path, int, int]] = []
        total_size = 0
        for source in candidates:
            source_stat = source.stat()
            size = source_stat.st_size
            if size > MAX_IMAGE_FILE_BYTES:
                raise WorkspaceLimitError(
                    f"Изображение «{source.name}» занимает {human_file_size(size)}; "
                    f"лимит — {human_file_size(MAX_IMAGE_FILE_BYTES)}."
                )
            total_size += size
            if total_size > MAX_TOTAL_IMAGE_BYTES:
                raise WorkspaceLimitError(
                    f"Общий размер изображений превышает {human_file_size(MAX_TOTAL_IMAGE_BYTES)}."
                )
            validated_sources.append((source, size, source_stat.st_mtime_ns))
        sources: list[SourceImage] = []
        for position, (source, size, modified_ns) in enumerate(validated_sources, start=1):
            stored = self.uploads_dir / unique_stored_name(position, source.name)
            if (
                not stored.exists()
                or stored.stat().st_size != size
                or stored.stat().st_mtime_ns != modified_ns
            ):
                shutil.copy2(source, stored)
            sources.append(SourceImage(source.name, stored))
        return sources

    def owns_path(self, path: Path, parent: Path | None = None) -> bool:
        allowed = (parent or self.root).resolve()
        try:
            path.resolve().relative_to(allowed)
            return True
        except ValueError:
            return False

    def require_owned_path(self, path: Path, parent: Path | None = None) -> Path:
        if not self.owns_path(path, parent):
            raise ValueError("Приложение отклонило путь за пределами рабочей папки проекта.")
        return path

    def output_path(self, filename: str) -> Path:
        if Path(filename).name != filename or Path(filename).suffix.lower() != ".mp4":
            raise ValueError("Некорректное внутреннее имя результата MP4.")
        return self.require_owned_path(self.output_dir / filename, self.output_dir)

    def _remove_owned_tree(self, path: Path) -> None:
        self.require_owned_path(path)
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def clear_intermediates(self) -> None:
        for directory in (self.prepared_dir, self.clips_dir, self.render_dir):
            self._remove_owned_tree(directory)
            directory.mkdir(parents=True, exist_ok=True)

    def cleanup_project(self, keep_output: bool = True, keep_logs: bool = True) -> None:
        if self.root.exists() or self.root.is_symlink():
            self.require_owned_path(self.root, TEMP_ROOT)
            if self.root.is_symlink():
                self.root.unlink(missing_ok=True)
            else:
                shutil.rmtree(self.root)
        if not keep_output and self.output_dir.exists():
            self.require_owned_path(self.output_dir, OUTPUT_ROOT)
            shutil.rmtree(self.output_dir)
        if not keep_logs and self.log_dir.exists():
            self.require_owned_path(self.log_dir, LOG_ROOT)
            shutil.rmtree(self.log_dir)

    @staticmethod
    def cleanup_stale(
        project_ids_to_keep: Iterable[str] = (),
        older_than_hours: float = 24.0,
    ) -> int:
        keep = set(project_ids_to_keep)
        removed_projects: set[str] = set()
        cutoff = time.time() - max(older_than_hours, 1.0) * 3600
        for storage_root in (TEMP_ROOT, OUTPUT_ROOT, LOG_ROOT):
            if not storage_root.exists():
                continue
            for directory in storage_root.iterdir():
                if (
                    directory.is_dir()
                    and not directory.is_symlink()
                    and _PROJECT_ID_RE.fullmatch(directory.name)
                    and directory.name not in keep
                    and directory.stat().st_mtime < cutoff
                ):
                    shutil.rmtree(directory)
                    removed_projects.add(directory.name)
        return len(removed_projects)
