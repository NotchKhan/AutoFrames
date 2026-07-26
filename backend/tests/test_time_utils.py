from __future__ import annotations

import pytest

from utils.time_utils import format_ms, frame_index, parse_display_time, seconds_to_ms


def test_format_ms() -> None:
    assert format_ms(3_735_750) == "01:02:15.750"


@pytest.mark.parametrize(("text", "expected"), [
    ("00:00:05.500", 5_500), ("01:02:15.750", 3_735_750), ("1:02.250", 62_250),
])
def test_parse_display_time(text: str, expected: int) -> None:
    assert parse_display_time(text) == expected


def test_seconds_decimal_conversion() -> None:
    assert seconds_to_ms("1.2345") == 1_235


def test_frame_boundary_rounding_does_not_accumulate() -> None:
    assert frame_index(1_000, 30) == 30
    assert frame_index(1_017, 30) == 31

