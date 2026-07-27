from __future__ import annotations

import asyncio
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
from services.audio_analyzer import AudioAnalysisError, AudioConcatError, AudioPause
from services.project_service import ProjectService
from services.speech_recognizer import (
    SpeechRecognitionError,
    SpeechSegment,
    SpeechTranscript,
    SpeechWord,
)


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
    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", None)
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", False)
    monkeypatch.setattr(project_service_module, "probe_audio_duration_ms", lambda _path, _probe: 14_000)
    monkeypatch.setattr(
        project_service_module,
        "detect_audio_pauses",
        lambda _path, _ffmpeg, _duration, **_kwargs: [
            AudioPause(4_700, 5_300),
            AudioPause(9_500, 10_100),
        ],
    )
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
    assert payload["audio_track_count"] == 1


def test_multiple_audio_tracks_are_concatenated_in_uploaded_order(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project(client)
    concat_calls: list[tuple[list[bytes], int]] = []

    def probe(path: Path, _ffprobe: Path) -> int:
        if path.name.startswith("audio_combined_"):
            return 7_000
        return {b"first-track": 3_000, b"second-track": 4_000}[path.read_bytes()]

    def concat(
        paths: list[Path],
        _ffmpeg: Path,
        destination: Path,
        total_duration_ms: int,
    ) -> Path:
        concat_calls.append(([path.read_bytes() for path in paths], total_duration_ms))
        destination.write_bytes(b"combined-audio")
        return destination

    monkeypatch.setattr(project_service_module, "probe_audio_duration_ms", probe)
    monkeypatch.setattr(project_service_module, "concatenate_audio_tracks", concat)
    tracks = [
        ("files", ("01_intro.wav", b"first-track", "audio/wav")),
        ("files", ("02_finish.mp3", b"second-track", "audio/mpeg")),
    ]

    response = client.post(f"/api/projects/{project_id}/audio", files=tracks)

    assert response.status_code == 200
    assert response.json()["uploaded_count"] == 2
    assert concat_calls == [([b"first-track", b"second-track"], 7_000)]
    record = service._records[project_id]
    assert record.audio_original_filenames == ["01_intro.wav", "02_finish.mp3"]
    assert record.audio_duration_ms == 7_000
    assert record.audio_path is not None
    assert record.audio_path.name.startswith("audio_combined_")
    assert record.audio_path.read_bytes() == b"combined-audio"
    assert list(record.workspace.uploads_dir.glob("audio_*")) == [record.audio_path]
    timeline = client.get(f"/api/projects/{project_id}/timeline").json()
    assert timeline["audio_track_count"] == 2
    assert timeline["audio_duration_ms"] == 7_000


def test_failed_multi_track_concat_preserves_previous_audio(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project(client)
    original = {"file": ("original.wav", b"original-track", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=original).status_code == 200
    record = service._records[project_id]
    old_path = record.audio_path
    old_duration = record.audio_duration_ms

    def fail_concat(*_args: object, **_kwargs: object) -> Path:
        raise AudioConcatError("expected test failure")

    monkeypatch.setattr(project_service_module, "concatenate_audio_tracks", fail_concat)
    replacement = [
        ("files", ("part-1.wav", b"replacement-one", "audio/wav")),
        ("files", ("part-2.wav", b"replacement-two", "audio/wav")),
    ]
    response = client.post(f"/api/projects/{project_id}/audio", files=replacement)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "audio_concat_failed"
    assert record.audio_path == old_path
    assert record.audio_original_filenames == ["original.wav"]
    assert record.audio_duration_ms == old_duration
    assert old_path is not None and old_path.is_file()
    assert list(record.workspace.uploads_dir.glob("audio_*")) == [old_path]


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


def test_images_without_timestamps_are_synced_to_audio_pauses(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    project_id = create_project(client)
    files = [
        ("files", ("scene_10.jpg", image_bytes(), "image/jpeg")),
        ("files", ("scene_2.jpg", image_bytes(), "image/jpeg")),
        ("files", ("scene_1.jpg", image_bytes(), "image/jpeg")),
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=audio).status_code == 200

    payload = client.get(f"/api/projects/{project_id}/timeline").json()
    assert payload["is_valid"] is True
    assert payload["timeline_mode"] == "audio_pauses"
    assert payload["detected_pauses"] == 2
    assert payload["transcription_used"] is False
    assert payload["analysis_method"] == "pauses"
    assert "ключ" in payload["analysis_warning"].lower()
    assert [row["original_filename"] for row in payload["items"]] == [
        "scene_1.jpg",
        "scene_2.jpg",
        "scene_10.jpg",
    ]
    assert [row["end_ms"] for row in payload["items"]] == [4_880, 9_680, 14_000]
    assert payload["difference_ms"] == 0


def test_high_quality_mode_uses_sentence_timestamps_and_caches_audio_analysis(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def recognize(
        _self: ProjectService,
        _record: object,
        _path: Path,
        _duration: int,
    ) -> SpeechTranscript:
        nonlocal calls
        calls += 1
        return SpeechTranscript(
            "ru",
            (
                SpeechWord("Первая.", 500, 4_800),
                SpeechWord("Вторая!", 5_400, 9_600),
                SpeechWord("Финал", 10_200, 13_500),
            ),
            (
                SpeechSegment("Первая.", 500, 4_800),
                SpeechSegment("Вторая!", 5_400, 9_600),
            ),
        )

    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ProjectService, "_recognize_speech", recognize)
    service._transcription_usage.clear()
    project_id = create_project(client)
    first_batch = [
        ("files", ("01.jpg", image_bytes(), "image/jpeg")),
        ("files", ("02.jpg", image_bytes(), "image/jpeg")),
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=first_batch).status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=audio).status_code == 200

    third = {"files": ("03.jpg", image_bytes(), "image/jpeg")}
    assert client.post(f"/api/projects/{project_id}/images", files=third).status_code == 200
    payload = client.get(f"/api/projects/{project_id}/timeline").json()

    assert calls == 1
    assert payload["transcription_used"] is True
    assert payload["analysis_method"] == "phrases_and_pauses"
    assert payload["detected_sentences"] == 3
    assert [row["boundary_kind"] for row in payload["items"]] == [
        "sentence_pause",
        "sentence_pause",
        "audio_end",
    ]
    assert payload["analysis_warning"] is None


def test_speech_provider_failure_falls_back_to_local_pauses(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def recognize(*_args: object, **_kwargs: object) -> SpeechTranscript:
        raise SpeechRecognitionError("provider unavailable")

    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ProjectService, "_recognize_speech", recognize)
    project_id = create_project(client)
    files = [
        ("files", ("01.jpg", image_bytes(), "image/jpeg")),
        ("files", ("02.jpg", image_bytes(), "image/jpeg")),
        ("files", ("03.jpg", image_bytes(), "image/jpeg")),
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=audio).status_code == 200

    payload = client.get(f"/api/projects/{project_id}/timeline").json()
    assert payload["is_valid"] is True
    assert payload["transcription_used"] is False
    assert payload["analysis_method"] == "pauses"
    assert "распознавание фраз недоступно" in payload["analysis_warning"].lower()


def test_more_sentences_than_frames_is_reported(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def recognize(*_args: object, **_kwargs: object) -> SpeechTranscript:
        return SpeechTranscript(
            "ru",
            (
                SpeechWord("Раз.", 500, 2_000),
                SpeechWord("Два.", 2_200, 4_500),
                SpeechWord("Три.", 5_200, 7_500),
                SpeechWord("Четыре.", 8_200, 13_000),
            ),
            (),
        )

    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ProjectService, "_recognize_speech", recognize)
    project_id = create_project(client)
    files = [
        ("files", ("01.jpg", image_bytes(), "image/jpeg")),
        ("files", ("02.jpg", image_bytes(), "image/jpeg")),
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=audio).status_code == 200

    payload = client.get(f"/api/projects/{project_id}/timeline").json()
    assert payload["detected_sentences"] == 4
    assert "некоторые соседние предложения" in payload["analysis_warning"].lower()


def test_invalid_semantic_plan_reports_real_image_count(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def recognize(*_args: object, **_kwargs: object) -> SpeechTranscript:
        return SpeechTranscript(
            "ru",
            (
                SpeechWord("длинная", 100, 6_500),
                SpeechWord("фраза", 6_600, 13_900),
            ),
            (),
        )

    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ProjectService, "_recognize_speech", recognize)
    project_id = create_project(client)
    files = [
        ("files", (f"{index:02d}.jpg", image_bytes(), "image/jpeg"))
        for index in range(1, 5)
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=audio).status_code == 200

    payload = client.get(f"/api/projects/{project_id}/timeline").json()
    assert payload["is_valid"] is False
    assert payload["analysis_method"] == "unavailable"
    assert payload["items"] == []
    assert "для 3 смен кадров" in payload["analysis_warning"].lower()


def test_all_audio_analysis_failures_fall_back_to_even_timeline(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def recognize(*_args: object, **_kwargs: object) -> SpeechTranscript:
        raise SpeechRecognitionError("provider unavailable")

    def detect(*_args: object, **_kwargs: object) -> list[AudioPause]:
        raise AudioAnalysisError("ffmpeg analysis failed")

    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ProjectService, "_recognize_speech", recognize)
    monkeypatch.setattr(project_service_module, "detect_audio_pauses", detect)
    project_id = create_project(client)
    files = [
        ("files", ("01.jpg", image_bytes(), "image/jpeg")),
        ("files", ("02.jpg", image_bytes(), "image/jpeg")),
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=audio).status_code == 200

    payload = client.get(f"/api/projects/{project_id}/timeline").json()
    assert payload["is_valid"] is True
    assert payload["analysis_method"] == "even"
    assert payload["items"][0]["end_ms"] == 7_000
    warning = payload["analysis_warning"].lower()
    assert "локальные паузы не определены" in warning
    assert "распознавание фраз недоступно" in warning


def test_unexpected_speech_programming_error_is_not_silently_degraded(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def recognize(*_args: object, **_kwargs: object) -> SpeechTranscript:
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ProjectService, "_recognize_speech", recognize)
    project_id = create_project(client)
    record = service._records[project_id]
    audio_path = record.workspace.uploads_dir / "voice.wav"
    audio_path.write_bytes(b"RIFF-valid-test-data")

    with pytest.raises(RuntimeError, match="unexpected bug"):
        asyncio.run(service._analyze_audio_for(record, audio_path, 14_000))


def test_prepared_transcription_audio_is_removed_after_provider_failure(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def prepare(
        _source: Path,
        _ffmpeg: Path,
        destination: Path,
        _duration_ms: int,
    ) -> Path:
        destination.write_bytes(b"temporary-mp3")
        return destination

    async def transcribe(*_args: object, **_kwargs: object) -> SpeechTranscript:
        raise SpeechRecognitionError("provider unavailable")

    monkeypatch.setattr(project_service_module, "prepare_transcription_audio", prepare)
    monkeypatch.setattr(project_service_module, "transcribe_audio", transcribe)
    project_id = create_project(client)
    record = service._records[project_id]
    source = record.workspace.uploads_dir / "voice.wav"
    source.write_bytes(b"RIFF-valid-test-data")

    with pytest.raises(SpeechRecognitionError):
        asyncio.run(service._recognize_speech(record, source, 14_000))
    assert list(record.workspace.prepared_dir.glob("transcription_*.mp3")) == []


def test_manual_timestamps_never_call_speech_recognition(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def recognize(*_args: object, **_kwargs: object) -> SpeechTranscript:
        nonlocal calls
        calls += 1
        raise AssertionError("manual mode must not call speech recognition")

    monkeypatch.setattr(project_service_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(project_service_module, "OPENAI_TRANSCRIPTION_ENABLED", True)
    monkeypatch.setattr(ProjectService, "_recognize_speech", recognize)
    project_id = create_project(client)
    upload_valid_project(client, project_id)

    payload = client.get(f"/api/projects/{project_id}/timeline").json()
    assert calls == 0
    assert not client.app.state.projects._transcription_usage
    assert payload["timeline_mode"] == "timestamps"
    assert payload["analysis_method"] == "manual"


def test_mixed_manual_and_automatic_filenames_are_rejected(client: TestClient) -> None:
    project_id = create_project(client)
    files = [
        ("files", ("[0-05]_manual.jpg", image_bytes(), "image/jpeg")),
        ("files", ("02_auto.jpg", image_bytes(), "image/jpeg")),
    ]
    response = client.post(f"/api/projects/{project_id}/images", files=files)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "mixed_timeline_mode"


@pytest.mark.parametrize(
    ("first_name", "second_name"),
    [
        ("[0-05]_manual.jpg", "02_auto.jpg"),
        ("01_auto.jpg", "[0-05]_manual.jpg"),
    ],
)
def test_mixed_mode_across_upload_batches_is_atomic(
    client: TestClient,
    service: ProjectService,
    first_name: str,
    second_name: str,
) -> None:
    project_id = create_project(client)
    assert client.post(
        f"/api/projects/{project_id}/images",
        files={"files": (first_name, image_bytes(), "image/jpeg")},
    ).status_code == 200

    response = client.post(
        f"/api/projects/{project_id}/images",
        files={"files": (second_name, image_bytes(), "image/jpeg")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "mixed_timeline_mode"
    record = service._records[project_id]
    assert [image.original_filename for image in record.images] == [first_name]
    assert len(list(record.workspace.uploads_dir.glob("image_*"))) == 1


def test_malformed_bracket_prefix_is_not_silently_treated_as_auto(client: TestClient) -> None:
    project_id = create_project(client)
    response = client.post(
        f"/api/projects/{project_id}/images",
        files={"files": ("[bad]_scene.jpg", image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_image_timestamp"


def test_render_rejects_scenes_shorter_than_one_output_frame(client: TestClient) -> None:
    project_id = create_project(client)
    files = [
        ("files", ("[0-00.010]_one.jpg", image_bytes(), "image/jpeg")),
        ("files", ("[0-00.020]_two.jpg", image_bytes(), "image/jpeg")),
    ]
    assert client.post(f"/api/projects/{project_id}/images", files=files).status_code == 200
    audio = {"file": ("voice.wav", b"RIFF-valid-test-data", "audio/wav")}
    assert client.post(f"/api/projects/{project_id}/audio", files=audio).status_code == 200
    response = client.post(f"/api/projects/{project_id}/render", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_render_settings"


@pytest.mark.parametrize(
    ("filename", "content", "mime", "code"),
    [
        ("../[0-05]_escape.jpg", b"data", "image/jpeg", "unsafe_filename"),
        ("[0-05]_wrong.jpg", b"data", "text/plain", "invalid_image_mime"),
        ("[0-05]_broken.jpg", b"not-an-image", "image/jpeg", "corrupted_image"),
        ("unsupported.gif", b"data", "image/gif", "unsupported_image_type"),
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


def test_cancel_render_while_waiting_for_worker_slot(
    client: TestClient,
    service: ProjectService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = create_project(client)
    upload_valid_project(client, project_id)

    class MustNotRunRenderer:
        def __init__(self, *_args: object) -> None:
            raise AssertionError("cancelled queued render must not start FFmpeg")

    monkeypatch.setattr(project_service_module, "VideoRenderer", MustNotRunRenderer)
    service._render_slots.acquire()
    try:
        assert client.post(f"/api/projects/{project_id}/render", json={}).status_code == 202
        assert client.get(f"/api/projects/{project_id}/status").json()["status"] == "queued"
        assert client.post(f"/api/projects/{project_id}/cancel").status_code == 200
    finally:
        service._render_slots.release()

    for _ in range(100):
        payload = client.get(f"/api/projects/{project_id}/status").json()
        if payload["status"] == "cancelled":
            break
        time.sleep(0.01)
    assert payload["status"] == "cancelled"
    assert payload["message"] == "Рендеринг отменён до запуска."


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
