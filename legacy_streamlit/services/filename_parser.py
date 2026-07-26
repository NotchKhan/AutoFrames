from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from config import IMAGE_EXTENSIONS


_PREFIX_RE = re.compile(r"^\[([^\]]*)\]")
_TWO_PART_RE = re.compile(r"^(\d+)-(\d{2})(?:\.(\d{3}))?$")
_THREE_PART_RE = re.compile(r"^(\d+)-(\d{2})-(\d{2})(?:\.(\d{3}))?$")


class TimestampParseError(ValueError):
    """Понятная пользователю ошибка временной метки в имени."""

@dataclass(frozen=True, slots=True)
class ParsedTimestamp:
    raw: str
    milliseconds: int


def _millis(fraction: str | None) -> int:
    return int((fraction or "0").ljust(3, "0"))


def parse_timestamp(filename: str) -> ParsedTimestamp:
    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise TimestampParseError(
            f"Файл «{filename}» имеет неподдерживаемое расширение «{suffix or 'без расширения'}». "
            f"Допустимы: {allowed}."
        )

    prefix = _PREFIX_RE.match(filename)
    if not prefix:
        raise TimestampParseError(
            f"Файл «{filename}» не содержит временную метку в начале имени. "
            "Ожидается, например, [1-15]_scene.jpg."
        )
    raw = prefix.group(1)
    two = _TWO_PART_RE.fullmatch(raw)
    if two:
        minutes_text, seconds_text, fraction = two.groups()
        seconds = int(seconds_text)
        if seconds > 59:
            raise TimestampParseError(
                f"Файл «{filename}» содержит неправильное количество секунд: {seconds_text}. "
                "Допустимый диапазон — от 00 до 59."
            )
        total = int(minutes_text) * 60_000 + seconds * 1_000 + _millis(fraction)
        return ParsedTimestamp(raw=f"[{raw}]", milliseconds=total)

    three = _THREE_PART_RE.fullmatch(raw)
    if three:
        hours_text, minutes_text, seconds_text, fraction = three.groups()
        minutes, seconds = int(minutes_text), int(seconds_text)
        if minutes > 59:
            raise TimestampParseError(
                f"Файл «{filename}» содержит неправильное количество минут в часовом формате: "
                f"{minutes_text}. Допустимый диапазон — от 00 до 59."
            )
        if seconds > 59:
            raise TimestampParseError(
                f"Файл «{filename}» содержит неправильное количество секунд: {seconds_text}. "
                "Допустимый диапазон — от 00 до 59."
            )
        total = (
            int(hours_text) * 3_600_000
            + minutes * 60_000
            + seconds * 1_000
            + _millis(fraction)
        )
        return ParsedTimestamp(raw=f"[{raw}]", milliseconds=total)

    if raw == "":
        detail = "пустая временная метка"
    else:
        detail = f"неподдерживаемая временная метка [{raw}]"
    raise TimestampParseError(
        f"Файл «{filename}» содержит {detail}. Поддерживаются [MM-SS], [MM-SS.mmm], "
        "[HH-MM-SS] и [HH-MM-SS.mmm]. Секунды всегда записываются двумя цифрами."
    )
