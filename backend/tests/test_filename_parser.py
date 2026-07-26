from __future__ import annotations

import pytest

from services.filename_parser import TimestampParseError, parse_timestamp


@pytest.mark.parametrize(("filename", "expected"), [
    ("[0-05]_image.jpg", 5_000),
    ("[1-02]_scene.png", 62_000),
    ("[12-30]_frame.WEBP", 750_000),
    ("[0-05.500]_кадр с пробелом.jpeg", 5_500),
    ("[00-01-05]_x.bmp", 65_000),
    ("[01-02-15.750]_много.точек.jpg", 3_735_750),
    ("[125-45]_long.jpg", 7_545_000),
])
def test_valid_timestamps(filename: str, expected: int) -> None:
    assert parse_timestamp(filename).milliseconds == expected


@pytest.mark.parametrize("filename", [
    "[0-60]_x.jpg", "[1-75]_x.jpg", "[abc]_x.jpg", "[1]_x.jpg",
    "[1-2-3-4]_x.jpg", "0-05_x.jpg", "[]_x.jpg", "[-1-05]_x.jpg",
    "[00-61-00]_x.jpg", "[0-05.5]_x.jpg", "[0-05.50]_x.jpg",
])
def test_invalid_timestamps(filename: str) -> None:
    with pytest.raises(TimestampParseError):
        parse_timestamp(filename)


def test_unsupported_extension() -> None:
    with pytest.raises(TimestampParseError, match="неподдерживаемое расширение"):
        parse_timestamp("[0-05]_x.gif")
