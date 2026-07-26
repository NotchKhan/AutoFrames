from __future__ import annotations

import base64
import errno
import hashlib
import mimetypes
from pathlib import Path


def safe_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def unique_stored_name(
    position: int,
    filename: str,
    content: bytes | memoryview | None = None,
) -> str:
    seed = content if content is not None else filename.encode("utf-8", errors="replace")
    digest = hashlib.sha256(seed).hexdigest()[:12]
    return f"{position:05d}_{digest}{safe_extension(filename)}"


def display_filename(untrusted_name: str) -> str:
    """Оставляет только имя: загруженный путь никогда не используется для записи."""
    cleaned = untrusted_name.replace("\x00", "").replace("/", "\\")
    name = Path(cleaned).name.strip()
    if not name:
        raise ValueError("Загруженный файл не имеет корректного имени.")
    return name


def image_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def human_file_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def friendly_os_error(action: str, error: OSError) -> str:
    if isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.EPERM}:
        return (
            f"{action}: нет прав записи. Переместите проект в пользовательскую папку "
            "и закройте программы, удерживающие файлы."
        )
    if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
        return f"{action}: закончилось свободное место на диске. Освободите место и повторите."
    return (
        f"{action}: ошибка файловой системы. Проверьте доступность диска, права записи "
        "и длину пути, затем повторите."
    )
