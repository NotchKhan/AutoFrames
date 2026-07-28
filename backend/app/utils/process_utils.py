from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Sequence


class ProcessCancelled(RuntimeError):
    """Процесс был корректно остановлен по запросу пользователя."""


class ProcessExecutionError(RuntimeError):
    def __init__(self, command_name: str, returncode: int, log_path: Path) -> None:
        if returncode == -9:
            message = (
                f"{command_name} был принудительно остановлен системой (сигнал 9). "
                "Обычно это означает нехватку оперативной памяти на сервере. "
                "Технические подробности сохранены в журнале проекта."
            )
        else:
            message = (
                f"{command_name} не смог завершить операцию (код {returncode}). "
                "Проверьте свободное место, права записи и входные файлы. "
                "Технические подробности сохранены в журнале проекта."
            )
        super().__init__(
            message
        )
        self.returncode = returncode
        self.log_path = log_path


def run_process(
    args: Sequence[str | Path],
    log_path: Path,
    cancel_event: threading.Event | None = None,
    cwd: Path | None = None,
) -> None:
    """Безопасно запускает процесс списком аргументов, ведёт лог и поддерживает отмену."""
    if not args:
        raise ValueError("Не указана команда для запуска.")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(value) for value in args]
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        log_file.write("\n$ " + subprocess.list2cmdline(command) + "\n")
        log_file.flush()
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creation_flags,
        )
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise ProcessCancelled("Операция отменена пользователем.")
            time.sleep(0.1)
        if process.returncode != 0:
            raise ProcessExecutionError(Path(command[0]).name, process.returncode, log_path)


def log_tail(path: Path, lines: int = 30) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])
