from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pytest

import services.workspace_manager as workspace_module
from services.workspace_manager import WorkspaceLimitError, WorkspaceManager


@dataclass
class FakeUpload:
    name: str
    data: bytes

    def getvalue(self) -> bytes:
        return self.data

    def getbuffer(self) -> memoryview:
        return memoryview(self.data)


@pytest.fixture
def isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace_module, "TEMP_ROOT", tmp_path / "temp")
    monkeypatch.setattr(workspace_module, "OUTPUT_ROOT", tmp_path / "output")
    monkeypatch.setattr(workspace_module, "LOG_ROOT", tmp_path / "logs")


def test_project_id_rejects_path_traversal(isolated_roots: None) -> None:
    with pytest.raises(ValueError, match="ID проекта"):
        WorkspaceManager("..\\outside")


def test_uploaded_names_are_internal_and_duplicates_do_not_collide(
    isolated_roots: None,
) -> None:
    workspace = WorkspaceManager("a" * 32)
    files = [
        FakeUpload("..\\..\\[0-05]_кадр.jpg", b"first"),
        FakeUpload("..\\..\\[0-05]_кадр.jpg", b"second"),
    ]
    sources = workspace.save_uploaded_images(files)
    assert [source.original_filename for source in sources] == [
        "[0-05]_кадр.jpg", "[0-05]_кадр.jpg",
    ]
    assert sources[0].stored_path != sources[1].stored_path
    assert all(workspace.owns_path(source.stored_path, workspace.uploads_dir) for source in sources)


def test_output_name_cannot_escape_workspace(isolated_roots: None) -> None:
    workspace = WorkspaceManager("b" * 32)
    with pytest.raises(ValueError, match="имя результата"):
        workspace.output_path("..\\outside.mp4")
    assert workspace.output_path("final_video.mp4").parent == workspace.output_dir


def test_file_count_and_size_limits(
    isolated_roots: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = WorkspaceManager("c" * 32)
    monkeypatch.setattr(workspace_module, "MAX_IMAGE_FILES", 1)
    with pytest.raises(WorkspaceLimitError, match="Максимум"):
        workspace.save_uploaded_images([
            FakeUpload("[0-01]_a.jpg", b"a"),
            FakeUpload("[0-02]_b.jpg", b"b"),
        ])

    monkeypatch.setattr(workspace_module, "MAX_IMAGE_FILES", 10)
    monkeypatch.setattr(workspace_module, "MAX_IMAGE_FILE_BYTES", 2)
    with pytest.raises(WorkspaceLimitError, match="размер одного"):
        workspace.save_uploaded_images([FakeUpload("[0-01]_a.jpg", b"abc")])


def test_cleanup_stale_ignores_non_project_directory(
    isolated_roots: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_workspace = WorkspaceManager("d" * 32)
    user_directory = workspace_module.TEMP_ROOT / "do-not-delete"
    user_directory.mkdir(parents=True)
    old_time = 1_000_000
    os.utime(old_workspace.root, (old_time, old_time))
    os.utime(user_directory, (old_time, old_time))
    monkeypatch.setattr(workspace_module.time, "time", lambda: old_time + 48 * 3600)
    assert WorkspaceManager.cleanup_stale(older_than_hours=24) == 1
    assert not old_workspace.root.exists()
    assert user_directory.exists()

