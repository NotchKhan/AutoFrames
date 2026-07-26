from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


_DISPLAY_RE = re.compile(r"^(?:(\d+):)?([0-5]?\d):([0-5]\d)(?:\.(\d{1,3}))?$")


def format_ms(milliseconds: int) -> str:
    """Форматирует целые миллисекунды как HH:MM:SS.mmm."""
    sign = "-" if milliseconds < 0 else ""
    value = abs(milliseconds)
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def parse_display_time(value: str) -> int:
    """Разбирает HH:MM:SS.mmm (или MM:SS.mmm) в миллисекунды."""
    text = value.strip()
    match = _DISPLAY_RE.fullmatch(text)
    if not match:
        raise ValueError(
            f"Время «{value}» должно иметь формат HH:MM:SS.mmm, например 00:01:02.250."
        )
    hours_text, minutes_text, seconds_text, fraction = match.groups()
    hours = int(hours_text or 0)
    minutes = int(minutes_text)
    if hours_text is not None and minutes > 59:
        raise ValueError("Минуты в часовом формате должны быть от 00 до 59.")
    millis = int((fraction or "0").ljust(3, "0"))
    return hours * 3_600_000 + minutes * 60_000 + int(seconds_text) * 1_000 + millis


def seconds_to_ms(value: str | float | Decimal) -> int:
    try:
        seconds = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Некорректное количество секунд: {value}") from exc
    return int((seconds * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ms_to_ffmpeg_time(milliseconds: int) -> str:
    return f"{Decimal(milliseconds) / Decimal(1000):.3f}"


def frame_index(milliseconds: int, fps: int) -> int:
    """Округляет абсолютную границу к ближайшему кадру без накопления ошибки."""
    numerator = milliseconds * fps
    return (numerator + 500) // 1000
