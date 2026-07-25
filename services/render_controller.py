from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from models.render import RenderResult


@dataclass(slots=True)
class RenderStatus:
    stage: str = "Ожидание"
    current: int = 0
    total: int = 1
    message: str = ""
    running: bool = False
    result: RenderResult | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=20))


class RenderController:
    """Фоновая задача позволяет Streamlit безопасно отправить сигнал отмены."""

    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._status = RenderStatus()
        self._thread: threading.Thread | None = None

    def _progress(self, stage: str, current: int, total: int, message: str) -> None:
        with self._lock:
            self._status.stage = stage
            self._status.current = current
            self._status.total = max(total, 1)
            self._status.message = message
            self._status.logs.append(message)

    def start(self, render_call: Callable[..., RenderResult], *args: Any, **kwargs: Any) -> None:
        if self.is_running:
            raise RuntimeError("Рендеринг уже выполняется.")
        self.cancel_event.clear()
        with self._lock:
            self._status = RenderStatus(stage="Проверка файлов", running=True)

        def worker() -> None:
            try:
                result = render_call(
                    *args, progress=self._progress, cancel_event=self.cancel_event, **kwargs
                )
            except Exception as exc:  # Защита фонового потока с сообщением в UI.
                result = RenderResult(False, error=f"Непредвиденная ошибка рендеринга: {exc}")
            with self._lock:
                self._status.result = result
                self._status.running = False

        self._thread = threading.Thread(target=worker, name="video-renderer", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> RenderStatus:
        with self._lock:
            return RenderStatus(
                stage=self._status.stage,
                current=self._status.current,
                total=self._status.total,
                message=self._status.message,
                running=self._status.running,
                result=self._status.result,
                logs=deque(self._status.logs, maxlen=20),
            )
