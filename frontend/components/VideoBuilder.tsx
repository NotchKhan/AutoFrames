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
  const currentStep = phase === "result" || renderActive ? 3 : timeline ? 2 : 0;

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
        message: "Останавливаем обработку и очищаем временные файлы.",
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
        <div className="header-inner">
          <a href="#top" className="brand" aria-label="AutoFrames — на главную">
            <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
            <span>AutoFrames</span>
          </a>
          <div className="header-actions">
            <a className="header-anchor" href="#workspace">Создать видео</a>
            <a
              className="creator-link"
              href="https://nexeraasia.vercel.app"
              target="_blank"
              rel="noreferrer"
              aria-label="Создано NEXERA — открыть сайт"
            >
              <span>By</span>
              <strong>NEXERA</strong>
              <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
                <path d="M5 11 11 5M6.5 5H11v4.5" />
              </svg>
            </a>
          </div>
        </div>
      </header>

      <div id="top" className="page-shell">
        <section className="hero">
          <div className="hero-copy">
            <span className="hero-badge"><i aria-hidden="true" /> Монтаж без рутины</span>
            <h1>Кадры становятся <em>готовым видео.</em></h1>
            <p>
              Добавьте изображения и озвучку — AutoFrames выстроит точный таймлайн,
              синхронизирует каждый кадр и подготовит ролик к публикации.
            </p>
            <div className="hero-benefits" aria-label="Преимущества">
              <span><i aria-hidden="true">✓</i> Точная синхронизация</span>
              <span><i aria-hidden="true">✓</i> До 500 кадров</span>
              <span><i aria-hidden="true">✓</i> Любой формат</span>
            </div>
            <a className="primary-button hero-button" href="#workspace">
              Начать сборку
              <svg aria-hidden="true" viewBox="0 0 20 20" fill="none"><path d="m7 4 6 6-6 6" /></svg>
            </a>
          </div>
          <div className="hero-visual" aria-hidden="true">
            <div className="studio-glow" />
            <div className="studio-card">
              <div className="studio-bar">
                <span><i /><i /><i /></span>
                <strong>Новый ролик</strong>
                <b>•••</b>
              </div>
              <div className="studio-preview">
                <div className="preview-shape preview-shape-one" />
                <div className="preview-shape preview-shape-two" />
                <span className="preview-play">
                  <svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5V7Z" /></svg>
                </span>
              </div>
              <div className="studio-timeline">
                <div className="timeline-ruler"><i /><i /><i /><i /><i /></div>
                <div className="timeline-clips"><i /><i /><i /><i /></div>
                <span className="timeline-cursor" />
              </div>
            </div>
            <div className="floating-card sync-card"><span>✓</span><div><small>Синхронизация</small><strong>Кадры готовы</strong></div></div>
            <div className="floating-card duration-card"><small>Длительность</small><strong>01:24</strong></div>
          </div>
        </section>

        <section id="workspace" className="workspace-heading">
          <div>
            <span className="eyebrow">Рабочая область</span>
            <h2>От файлов до готового ролика</h2>
          </div>
          <nav className="stepper" aria-label="Этапы проекта">
            {["Файлы", "Таймлайн", "Настройки", "Результат"].map((label, index) => (
              <div className={`step ${index < currentStep ? "done" : ""} ${index === currentStep ? "active" : ""}`} key={label}>
                <span>{index < currentStep ? "✓" : index + 1}</span>
                <strong>{label}</strong>
              </div>
            ))}
          </nav>
        </section>

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
            <p>Файл проверен и полностью готов к скачиванию и публикации.</p>
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

      <footer className="site-footer">
        <a href="#top" className="footer-brand">AutoFrames</a>
        <p>Собирайте истории, а не таймлайны.</p>
        <a href="https://nexeraasia.vercel.app" target="_blank" rel="noreferrer">By NEXERA ↗</a>
      </footer>
    </main>
  );
}
