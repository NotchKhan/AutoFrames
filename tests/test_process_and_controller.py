from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from models.render import RenderResult
from services.render_controller import RenderController
from utils.process_utils import ProcessCancelled, run_process


def test_running_process_can_be_cancelled_safely(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ProcessCancelled):
        run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            tmp_path / "cancel.log",
            cancel_event=cancel,
        )


def test_controller_blocks_repeated_start_and_accepts_cancel() -> None:
    controller = RenderController()

    def render_call(*, progress: object, cancel_event: threading.Event) -> RenderResult:
        del progress
        cancel_event.wait(timeout=2)
        return RenderResult(False, cancelled=True, error="cancelled")

    controller.start(render_call)
    with pytest.raises(RuntimeError, match="уже выполняется"):
        controller.start(render_call)
    controller.cancel()
    deadline = time.monotonic() + 3
    while controller.is_running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not controller.is_running
    assert controller.snapshot().result is not None
