from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from utils.time_utils import format_ms


@dataclass(frozen=True, slots=True)
class SourceImage:
    original_filename: str
    stored_path: Path


@dataclass(slots=True)
class TimelineItem:
    index: int
    original_filename: str
    stored_path: Path
    parsed_timestamp: str
    start_ms: int
    end_ms: int
    duration_ms: int
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manually_overridden: bool = False

    @property
    def start_formatted(self) -> str:
        return format_ms(self.start_ms)

    @property
    def end_formatted(self) -> str:
        return format_ms(self.end_ms)

    @property
    def duration_formatted(self) -> str:
        return format_ms(self.duration_ms)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    message: str
    filename: str | None = None
    critical: bool = True
