from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageColor, ImageFilter, ImageOps, UnidentifiedImageError

from config import MAX_IMAGE_PIXELS
from models.render import VideoSettings


class ImageValidationError(ValueError):
    """Понятная пользователю ошибка декодирования изображения."""


_EXPECTED_FORMATS: dict[str, set[str]] = {
    ".png": {"PNG"},
    ".jpg": {"JPEG"},
    ".jpeg": {"JPEG"},
    ".webp": {"WEBP"},
    ".bmp": {"BMP", "DIB"},
}


def _validate_metadata(image: Image.Image, path: Path, original_filename: str) -> None:
    expected = _EXPECTED_FORMATS.get(path.suffix.lower(), set())
    actual = (image.format or "").upper()
    if actual not in expected:
        shown = actual or "не определён"
        raise ImageValidationError(
            f"Изображение «{original_filename}» имеет содержимое формата {shown}, "
            f"не соответствующее расширению {path.suffix or 'без расширения'}. "
            "Пересохраните файл с правильным расширением."
        )
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ImageValidationError(
            f"Изображение «{original_filename}» имеет недопустимый размер {width}×{height}. "
            f"Максимум — {MAX_IMAGE_PIXELS:,} пикселей."
        )


def validate_image(path: Path, original_filename: str) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            _validate_metadata(image, path, original_filename)
            image.verify()
        with Image.open(path) as image:
            oriented = ImageOps.exif_transpose(image)
            return oriented.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageValidationError(
            f"Изображение «{original_filename}» повреждено или не поддерживается Pillow: {exc}"
        ) from exc


def prepare_image(source: Path, destination: Path, settings: VideoSettings) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = (settings.width, settings.height)
    try:
        with Image.open(source) as opened:
            _validate_metadata(opened, source, source.name)
            image = ImageOps.exif_transpose(opened).convert("RGB")
            if settings.scale_mode == "cover":
                canvas = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            elif settings.scale_mode == "fit_blur":
                background = ImageOps.fit(
                    image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
                ).filter(ImageFilter.GaussianBlur(radius=max(size) / 45))
                foreground = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
                position = ((size[0] - foreground.width) // 2, (size[1] - foreground.height) // 2)
                background.paste(foreground, position)
                canvas = background
            else:
                try:
                    color = ImageColor.getrgb(settings.background_color)
                except ValueError as exc:
                    raise ImageValidationError(
                        f"Некорректный цвет фона: {settings.background_color}."
                    ) from exc
                canvas = Image.new("RGB", size, color)
                foreground = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
                position = ((size[0] - foreground.width) // 2, (size[1] - foreground.height) // 2)
                canvas.paste(foreground, position)
            canvas.save(destination, format="PNG", optimize=False)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageValidationError(f"Не удалось подготовить изображение «{source.name}»: {exc}") from exc


def create_black_frame(destination: Path, width: int, height: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), "black").save(destination, format="PNG")


def create_thumbnail(source: Path, destination: Path, max_size: tuple[int, int] = (160, 90)) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as opened:
            _validate_metadata(opened, source, source.name)
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            image.save(destination, format="JPEG", quality=80)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, ImageValidationError):
            raise
        raise ImageValidationError(f"Не удалось создать миниатюру «{source.name}»: {exc}") from exc
