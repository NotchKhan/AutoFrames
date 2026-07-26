from __future__ import annotations

import io
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import services.project_service as project_service_module
import services.video_renderer as renderer_module
import services.workspace_manager as workspace_module
from main import create_app
from models.render import RenderResult
from services.project_service import ProjectService


def image_bytes(image_format: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), "#7c3aed").save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectService:
    temp_root = tmp_path / "projects"
    output_root = tmp_path / "output"
    log_root = tmp_path / "logs"
    for directory in (temp_root, output_root, log_root):
        directory.mkdir()
    monkeypatch.setattr(workspace_module, "TEMP_ROOT", temp_root)
    monkeypatch.setattr(workspace_module, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(workspace_module, "LOG_ROOT", log_root)
    monkeypatch.setattr(renderer_module, "OUTPUT_ROOT", output_root)
    monkeypatch.setattr(project_service_module, "probe_audio_duration_ms", lambda _path, _probe: 14_000)
    current = ProjectService()
    current.ffmpeg_path = Path("ffmpeg")
    current.ffprobe_path = Path("ffprobe")
    return current


@pytest.fixture
def client(service: ProjectService) -> TestClient:
    with TestClient(create_app(service)) as current:
        yield current


def create_project(client: TestClient) -> str:
    response = client.post("/api/projects")
    assert response.status_code == 201
    return response.json()["project_id"]


def upload_valid_project(client: TestClient, project_id: str) -> None:
    images = [
        ("files", ("[0-14]_второй кадр.jpg", image_bytes(), "image/jpeg")),
        ("files", ("[0-05]_first.jpg", image_bytes(), "image/jpeg")),
    ]
    response = client.post(f"/api/projects/{project_id}/images", files=images)
    assert response.status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    response = client.post(f"/api/projects/{project_id}/audio", files=audio)
    assert response.status_code == 200


def test_health_and_project_creation(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "autoframes-backend",
        "ffmpeg": True,
        "ffprobe": True,
    }
    project_id = create_project(client)
    assert len(project_id) == 32


def test_upload_and_sorted_timeline(client: TestClient) -> None:
    project_id = create_project(client)
    upload_valid_project(client, project_id)
    response = client.get(f"/api/projects/{project_id}/timeline")
    assert response.status_code == 200
    payload = response.json()
    assert payload["is_valid"] is True
    assert [row["original_filename"] for row in payload["items"]] == [
        "[0-05]_first.jpg",
        "[0-14]_второй кадр.jpg",
    ]
    assert [(row["start_ms"], row["end_ms"], row["duration_ms"]) for row in payload["items"]] == [
        (0, 5_000, 5_000),
        (5_000, 14_000, 9_000),
    ]
    assert payload["difference_ms"] == 0


def test_duplicate_timestamps_are_reported(client: TestClient) -> None:
    project_id = create_project(client)
    files = [
        ("files", ("[0-05]_one.png", image_bytes("PNG"), "image/png")),
        ("files", ("[0-05]_два.png", image_bytes("PNG"), "image/png")),
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    response = client.get(f"/api/projects/{project_id}/timeline")
    assert response.status_code == 200
    assert response.json()["is_valid"] is False
    assert any("одинаковое время окончания" in issue["message"] for issue in response.json()["issues"])


@pytest.mark.parametrize(
    ("filename", "content", "mime", "code"),
    [
        ("../[0-05]_escape.jpg", b"data", "image/jpeg", "unsafe_filename"),
        ("[0-05]_wrong.jpg", b"data", "text/plain", "invalid_image_mime"),
        ("[0-05]_broken.jpg", b"not-an-image", "image/jpeg", "corrupted_image"),
        ("without-mark.jpg", b"data", "image/jpeg", "invalid_image_timestamp"),
    ],
)
def test_secure_image_upload_errors(
    client: TestClient,
    filename: str,
    content: bytes,
    mime: str,
    code: str,
) -> None:
    project_id = create_project(client)
    response = client.post(
        f"/api/projects/{project_id}/images",
        files={"files": (filename, content, mime)},
    )
    assert response.status_code in {415, 422}
    assert response.json()["error"]["code"] == code


def test_render_requires_audio(client: TestClient) -> None:
    project_id = create_project(client)
    files = {"files": ("[0-05]_frame.jpg", image_bytes(), "image/jpeg")}
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    response = client.post(f"/api/projects/{project_id}/render", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "audio_missing"


def test_fake_render_status_and_download(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project(client)
    upload_valid_project(client, project_id)

    class FakeRenderer:
        def __init__(self, _ffmpeg: Path, _ffprobe: Path, workspace: workspace_module.WorkspaceManager) -> None:
            self.workspace = workspace

        def render(self, *_args: object, progress: object, **_kwargs: object) -> RenderResult:
            callback = progress
            assert callable(callback)
            callback("Создание видеоклипов", 1, 1, "Тестовый клип создан")
            output = self.workspace.output_path("final_video.mp4")
            output.write_bytes(b"fake-mp4")
            return RenderResult(True, output, media_info={"duration_ms": 14_000})

    monkeypatch.setattr(project_service_module, "VideoRenderer", FakeRenderer)
    response = client.post(f"/api/projects/{project_id}/render", json={})
    assert response.status_code == 202
    for _ in range(100):
        status_response = client.get(f"/api/projects/{project_id}/status")
        if status_response.json()["status"] == "completed":
            break
        time.sleep(0.01)
    payload = status_response.json()
    assert payload["status"] == "completed"
    assert payload["result_ready"] is True
    assert payload["progress_percent"] == 100.0
    assert client.get(f"/api/projects/{project_id}/timeline").status_code == 200
    assert client.get(f"/api/projects/{project_id}/status").json()["status"] == "completed"
    download = client.get(f"/api/projects/{project_id}/result")
    assert download.status_code == 200
    assert download.content == b"fake-mp4"


def test_cancel_render(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = create_project(client)
    upload_valid_project(client, project_id)

    class WaitingRenderer:
        def __init__(self, *_args: object) -> None:
            return None

        def render(self, *_args: object, cancel_event: object, **_kwargs: object) -> RenderResult:
            event = cancel_event
            assert hasattr(event, "is_set")
            for _ in range(200):
                if event.is_set():
                    return RenderResult(False, cancelled=True, error="Рендеринг отменён пользователем.")
                time.sleep(0.005)
            return RenderResult(False, error="Тест не получил отмену.")

    monkeypatch.setattr(project_service_module, "VideoRenderer", WaitingRenderer)
    assert client.post(f"/api/projects/{project_id}/render", json={}).status_code == 202
    cancel = client.post(f"/api/projects/{project_id}/cancel")
    assert cancel.status_code == 200
    for _ in range(100):
        payload = client.get(f"/api/projects/{project_id}/status").json()
        if payload["status"] == "cancelled":
            break
        time.sleep(0.01)
    assert payload["status"] == "cancelled"


def test_delete_and_not_found(client: TestClient) -> None:
    project_id = create_project(client)
    response = client.delete(f"/api/projects/{project_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    missing = client.get(f"/api/projects/{project_id}/status")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "project_not_found"


def test_expired_project_returns_gone(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project(client)
    monkeypatch.setattr(project_service_module, "PROJECT_TTL_HOURS", 0.001)
    service._records[project_id].touched_at = datetime.now(UTC) - timedelta(hours=1)
    response = client.get(f"/api/projects/{project_id}/status")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "project_expired"


def test_busy_project_and_unknown_route_use_error_envelope(
    client: TestClient,
    service: ProjectService,
) -> None:
    project_id = create_project(client)
    service._records[project_id].mutation_in_progress = True
    busy = client.get(f"/api/projects/{project_id}/timeline")
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "project_busy"
    unknown = client.get("/api/unknown-route")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "http_404"
