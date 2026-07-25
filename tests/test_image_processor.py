from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from models.render import VideoSettings
from services.image_processor import ImageValidationError, prepare_image, validate_image


@pytest.mark.parametrize("mode", ["cover", "fit_blur", "fit_color"])
def test_prepare_cyrillic_image_with_spaces(tmp_path: Path, mode: str) -> None:
    source = tmp_path / "[0-05]_кадр с пробелом.JPG"
    destination = tmp_path / f"готовый кадр {mode}.png"
    Image.new("RGB", (320, 180), "red").save(source, format="JPEG")

    assert validate_image(source, source.name) == (320, 180)
    settings = VideoSettings(
        width=200,
        height=200,
        scale_mode=mode,  # type: ignore[arg-type]
        background_color="#123456",
    )
    prepare_image(source, destination, settings)

    with Image.open(destination) as result:
        assert result.size == (200, 200)
        assert result.mode == "RGB"


def test_corrupted_image_has_clear_error(tmp_path: Path) -> None:
    source = tmp_path / "[0-05]_повреждённый файл.png"
    source.write_bytes(b"not an image")
    with pytest.raises(ImageValidationError, match="повреждено или не поддерживается"):
        validate_image(source, source.name)
