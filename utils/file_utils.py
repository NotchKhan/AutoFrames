from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path


def safe_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def unique_stored_name(position: int, filename: str, content: bytes | None = None) -> str:
    seed = content if content is not None else filename.encode("utf-8", errors="replace")
    digest = hashlib.sha256(seed).hexdigest()[:12]
    return f"{position:05d}_{digest}{safe_extension(filename)}"


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

