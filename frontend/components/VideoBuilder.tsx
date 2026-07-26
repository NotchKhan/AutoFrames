"use client";

import { useEffect, useMemo, useState } from "react";

import {
  ApiClientError,
  cancelRender,
  createProject,
  deleteImage,
  deleteProject,
  getStatus,
  getTimeline,
  resultUrl,
  startRender,
  uploadAudio,
  uploadImages,
} from "@/lib/api";
import { formatMilliseconds, humanFileSize } from "@/lib/time";
import {
  DEFAULT_RENDER_SETTINGS,
  type RenderPayload,
  type StatusResponse,
  type TimelineResponse,
} from "@/lib/types";
import { FileUpload } from "@/components/FileUpload";
import { RenderProgress } from "@/components/RenderProgress";
import { SettingsPanel } from "@/components/SettingsPanel";
import { TimelineTable } from "@/components/TimelineTable";


type UiPhase = "empty" | "uploading" | "review" | "rendering" | "result" | "error";

const QUEUED_STATUS: StatusResponse = {
  project_id: "",
  status: "queued",
  stage: "Очередь",
  progress_percent: 0,
  current: 0,
  total: 0,
  completed_operations: 0,
  message: "Задача рендеринга принята.",
  recent_logs: ["Задача рендеринга принята."],
  error: null,
  result_ready: false,
  media_info: {},
};


function messageFromError(error: unknown): string {
  if (error instanceof ApiClientError) return error.message;
  if (error instanceof Error) return error.message;
  return "Произошла непредвиденная ошибка.";
}


export function VideoBuilder() {
  const [images, setImages] = useState<File[]>([]);
  const [audio, setAudio] = useState<File | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [settings, setSettings] = useState<RenderPayload>(DEFAULT_RENDER_SETTINGS);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [phase, setPhase] = useState<UiPhase>("empty");
  const [busyMessage, setBusyMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  const renderActive = status !== null && ["queued", "rendering", "cancelling"].includes(status.status);
  const busy = phase === "uploading" || renderActive;
  const currentStep = phase === "result" ? 4 : renderActive ? 4 : timeline ? 3 : images.length || audio ? 1 : 0;

  const totalUploadSize = useMemo(
    () => images.reduce((sum, file) => sum + file.size, 0) + (audio?.size ?? 0),
    [images, audio],
  );

  useEffect(() => {
    if (!projectId || !renderActive) return;
    const activeProjectId = projectId;
    let stopped = false;

    async function poll() {
      try {
        const next = await getStatus(activeProjectId);
        if (stopped) return;
        setStatus(next);
        if (next.status === "completed") {
          setPhase("result");
        } else if (next.status === "failed" || next.status === "cancelled") {
          setPhase(next.status === "failed" ? "error" : "review");
          if (next.error) setError(next.error);
        }
      } catch (pollError) {
        if (!stopped) {
          setError(messageFromError(pollError));
          setPhase("error");
        }
      }
    }

    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [projectId, renderActive]);

  async function uploadProject() {
    if (!images.length || !audio) return;
    setPhase("uploading");
    setError(null);
    setTimeline(null);
    setStatus(null);
    try {
      if (projectId) {
        await deleteProject(projectId).catch(() => undefined);
      }
      setBusyMessage("Создаём изолированную рабочую область…");
      const project = await createProject();
      setProjectId(project.project_id);
      setBusyMessage(`Загружаем ${images.length} изображений…`);
      await uploadImages(project.project_id, images);
      setBusyMessage("Загружаем и проверяем аудиодорожку…");
      await uploadAudio(project.project_id, audio);
      setBusyMessage("Строим непрерывный таймлайн…");
      const checked = await getTimeline(project.project_id);
      setTimeline(checked);
      const difference = checked.difference_ms ?? 0;
      setSettings({
        ...DEFAULT_RENDER_SETTINGS,
        end_mode: difference < -50 ? "trim_video" : "extend_last",
      });
      setPhase("review");
    } catch (uploadError) {
      setError(messageFromError(uploadError));
      setPhase("error");
    } finally {
      setBusyMessage("");
    }
  }

  async function removeImage(imageId: string) {
    if (!projectId) return;
    setError(null);
    try {
      await deleteImage(projectId, imageId);
      setTimeline(await getTimeline(projectId));
    } catch (deleteError) {
      setError(messageFromError(deleteError));
    }
  }

  async function renderVideo() {
    if (!projectId || !timeline?.is_valid) return;
    setError(null);
    try {
      await startRender(projectId, settings);
      setStatus({ ...QUEUED_STATUS, project_id: projectId });
      setPhase("rendering");
    } catch (renderError) {
      setError(messageFromError(renderError));
      setPhase("error");
    }
  }

  async function stopRender() {
    if (!projectId) return;
    try {
      await cancelRender(projectId);
      setStatus((current) => current ? {
        ...current,
        status: "cancelling",
        stage: "Отмена",
        message: "Backend останавливает FFmpeg и очищает временные файлы.",
      } : current);
    } catch (cancelError) {
      setError(messageFromError(cancelError));
    }
  }

  async function newProject() {
    const previousId = projectId;
    setImages([]);
    setAudio(null);
    setProjectId(null);
    setTimeline(null);
    setSettings(DEFAULT_RENDER_SETTINGS);
    setStatus(null);
    setPhase("empty");
    setError(null);
    if (previousId) {
      await deleteProject(previousId).catch(() => undefined);
    }
  }

  const mediaInfo = status?.media_info ?? {};

  return (
    <main>
      <header className="site-header">
        <a href="#top" className="brand" aria-label="AutoFrames — на главную">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span>AutoFrames</span>
        </a>
        <div className="header-status">
          <span className="live-dot" />
          FFmpeg backend
        </div>
      </header>

      <div id="top" className="page-shell">
        <section className="hero">
          <div className="hero-copy">
            <span className="hero-badge">Локальная логика · облачный интерфейс</span>
            <h1>Автоматическая сборка <em>видео из кадров</em></h1>
            <p>
              Время окончания каждого кадра берётся из имени изображения. AutoFrames сортирует
              сотни файлов, проверяет синхронизацию и собирает готовый MP4 через FFmpeg.
            </p>
          </div>
          <div className="hero-visual" aria-hidden="true">
            <div className="frame-stack frame-back" />
            <div className="frame-stack frame-middle" />
            <div className="frame-stack frame-front">
              <span className="play-symbol">▶</span>
              <div className="visual-timeline"><i /><i /><i /><i /></div>
            </div>
          </div>
        </section>

        <nav className="stepper" aria-label="Этапы проекта">
          {["Файлы", "Таймлайн", "Настройки", "Результат"].map((label, index) => (
            <div className={`step ${index < currentStep ? "done" : ""} ${index === currentStep ? "active" : ""}`} key={label}>
              <span>{index < currentStep ? "✓" : index + 1}</span>
              <strong>{label}</strong>
            </div>
          ))}
        </nav>

        {error && (
          <div className="global-error" role="alert">
            <span aria-hidden="true">!</span>
            <div>
              <strong>Не удалось выполнить операцию</strong>
              <p>{error}</p>
            </div>
            <button type="button" aria-label="Закрыть сообщение" onClick={() => setError(null)}>×</button>
          </div>
        )}

        <FileUpload
          images={images}
          audio={audio}
          disabled={busy}
          onImagesChange={setImages}
          onAudioChange={setAudio}
          onSubmit={() => void uploadProject()}
        />

        {phase === "uploading" && (
          <section className="panel loading-state" aria-live="polite">
            <span className="large-spinner" aria-hidden="true" />
            <h2>{busyMessage || "Проверяем файлы…"}</h2>
            <p>{images.length} кадров · {humanFileSize(totalUploadSize)}</p>
          </section>
        )}

        {timeline && projectId && phase !== "uploading" && (
          <TimelineTable
            projectId={projectId}
            timeline={timeline}
            disabled={busy}
            onDelete={(imageId) => void removeImage(imageId)}
          />
        )}

        {timeline && phase !== "uploading" && (
          <SettingsPanel
            value={settings}
            timeline={timeline}
            disabled={busy}
            onChange={setSettings}
            onRender={() => void renderVideo()}
          />
        )}

        {status && (renderActive || status.status === "cancelled" || status.status === "failed") && (
          <RenderProgress status={status} onCancel={() => void stopRender()} />
        )}

        {phase === "result" && projectId && status?.result_ready && (
          <section className="panel result-panel" aria-labelledby="result-title">
            <div className="result-icon" aria-hidden="true">✓</div>
            <span className="eyebrow">Готово</span>
            <h2 id="result-title">Видео успешно собрано</h2>
            <p>Backend проверил контейнер, кодеки, разрешение, FPS и длительность результата.</p>
            <div className="result-specs">
              <span>{String(mediaInfo.width ?? settings.video.width)}×{String(mediaInfo.height ?? settings.video.height)}</span>
              <span>{String(mediaInfo.fps ?? settings.video.fps)} FPS</span>
              <span>{String(mediaInfo.video_codec ?? "H.264").toUpperCase()}</span>
              <span>{String(mediaInfo.audio_codec ?? "AAC").toUpperCase()}</span>
              {typeof mediaInfo.duration_ms === "number" && <span>{formatMilliseconds(mediaInfo.duration_ms)}</span>}
              {typeof mediaInfo.size_bytes === "number" && <span>{humanFileSize(mediaInfo.size_bytes)}</span>}
            </div>
            <div className="result-actions">
              <a className="primary-button download-button" href={resultUrl(projectId)} download="final_video.mp4">
                Скачать MP4
                <span aria-hidden="true">↓</span>
              </a>
              <button type="button" className="secondary-button" onClick={() => void newProject()}>
                Создать новый проект
              </button>
            </div>
          </section>
        )}
      </div>

      <footer>
        <p>AutoFrames · точная синхронизация кадров с аудио</p>
        <span>Next.js + FastAPI + FFmpeg</span>
      </footer>
    </main>
  );
}
