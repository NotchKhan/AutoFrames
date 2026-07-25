from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from config import IMAGE_EXTENSIONS, LOG_ROOT, OUTPUT_ROOT, TEMP_ROOT
from models.timeline import SourceImage
from utils.file_utils import unique_stored_name


class UploadedFileLike(Protocol):
    name: str

    def getvalue(self) -> bytes: ...


class WorkspaceManager:
    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id or uuid.uuid4().hex
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
        sources: list[SourceImage] = []
        for position, uploaded in enumerate(files, start=1):
            data = uploaded.getvalue()
            stored = self.uploads_dir / unique_stored_name(position, uploaded.name, data)
            if not stored.exists():
                stored.write_bytes(data)
            sources.append(SourceImage(uploaded.name, stored))
        return sources

    def save_uploaded_audio(self, uploaded: UploadedFileLike) -> Path:
        data = uploaded.getvalue()
        stored = self.uploads_dir / ("audio_" + unique_stored_name(0, uploaded.name, data))
        if not stored.exists():
            stored.write_bytes(data)
        return stored

    def import_folder(self, folder: Path) -> list[SourceImage]:
        resolved = folder.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"«{resolved}» не является папкой.")
        candidates = sorted(
            (path for path in resolved.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
            key=lambda path: path.name.casefold(),
        )
        sources: list[SourceImage] = []
        for position, source in enumerate(candidates, start=1):
            stored = self.uploads_dir / unique_stored_name(position, source.name)
            shutil.copy2(source, stored)
            sources.append(SourceImage(source.name, stored))
        return sources

    def clear_intermediates(self) -> None:
        for directory in (self.prepared_dir, self.clips_dir, self.render_dir):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True, exist_ok=True)

    def cleanup_project(self, keep_output: bool = True) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        if not keep_output and self.output_dir.exists():
            shutil.rmtree(self.output_dir)

    @staticmethod
    def cleanup_stale(project_ids_to_keep: Iterable[str] = ()) -> int:
        keep = set(project_ids_to_keep)
        count = 0
        if TEMP_ROOT.exists():
            for directory in TEMP_ROOT.iterdir():
                if directory.is_dir() and directory.name not in keep:
                    shutil.rmtree(directory)
                    count += 1
        return count

