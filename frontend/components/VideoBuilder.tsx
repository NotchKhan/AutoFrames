"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiClientError,
  cancelRender,
  createProject,
  deleteImage,
  deleteProject,
  getStatus,
  getTimeline,
  isTransientApiError,
  resultUrl,
  setSyncStrategy,
  startRender,
  uploadAudio,
} from "@/lib/api";
import { prepareImagesForUpload, uploadImagesInBatches } from "@/lib/imageUpload";
import { formatMilliseconds, humanFileSize } from "@/lib/time";
import {
  DEFAULT_RENDER_SETTINGS,
  type RenderPayload,
  type StatusResponse,
  type SyncStrategy,
  type TimelineResponse,
} from "@/lib/types";
import { FileUpload } from "@/components/FileUpload";
import { RenderProgress } from "@/components/RenderProgress";
import { SettingsPanel } from "@/components/SettingsPanel";
import { TimelineTable } from "@/components/TimelineTable";


type UiPhase = "empty" | "uploading" | "review" | "rendering" | "result" | "error";

const MIB = 1024 * 1024;
const PROJECT_UPLOAD_SOFT_LIMIT_BYTES = 112 * MIB;

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


function createRenderRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}


function displayFps(value: unknown, fallback: number): string {
  const fps = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return fps.toFixed(2).replace(/\.?0+$/, "");
}


export function VideoBuilder() {
  const [images, setImages] = useState<File[]>([]);
  const [audio, setAudio] = useState<File[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [settings, setSettings] = useState<RenderPayload>(DEFAULT_RENDER_SETTINGS);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [phase, setPhase] = useState<UiPhase>("empty");
  const [busyMessage, setBusyMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [deletingImageId, setDeletingImageId] = useState<string | null>(null);
  const [renderStarting, setRenderStarting] = useState(false);
  const [syncingStrategy, setSyncingStrategy] = useState(false);
  const [strategyStateUncertain, setStrategyStateUncertain] = useState(false);
  const renderStartInFlight = useRef(false);
  const syncStrategyInFlight = useRef(false);

  const renderActive = status !== null && ["queued", "rendering", "cancelling"].includes(status.status);
  const busy = phase === "uploading" || renderStarting || syncingStrategy || renderActive;
  const currentStep = phase === "result" || phase === "rendering" || renderActive ? 3 : timeline ? 2 : 0;

  const totalUploadSize = useMemo(
    () => images.reduce((sum, file) => sum + file.size, 0)
      + audio.reduce((sum, file) => sum + file.size, 0),
    [images, audio],
  );

  useEffect(() => {
    if (!projectId || !renderActive) return;
    const activeProjectId = projectId;
    let stopped = false;
    let timer: number | null = null;
    let consecutiveFailures = 0;

    function schedule(delayMs: number) {
      if (!stopped) timer = window.setTimeout(() => void poll(), delayMs);
    }

    async function poll() {
      let continuePolling = true;
      try {
        const next = await getStatus(activeProjectId);
        if (stopped) return;
        const hadConnectionFailures = consecutiveFailures > 0;
        consecutiveFailures = 0;
        if (next.status === "completed") {
          continuePolling = false;
          if (next.result_ready) {
            setStatus(next);
            setError(null);
            setPhase("result");
          } else {
            const missingResult = "Сервер завершил обработку, но готовый MP4 недоступен. Запустите сборку ещё раз.";
            setStatus({
              ...next,
              status: "failed",
              stage: "Ошибка результата",
              message: missingResult,
              error: missingResult,
            });
            setError(missingResult);
            setPhase("error");
          }
        } else if (next.status === "failed") {
          continuePolling = false;
          setStatus(next);
          setError(next.error ?? next.message);
          setPhase("error");
        } else if (next.status === "cancelled") {
          continuePolling = false;
          setStatus(next);
          setError(null);
          setPhase("review");
        } else if (["queued", "rendering", "cancelling"].includes(next.status)) {
          setStatus(next);
          setPhase("rendering");
          if (hadConnectionFailures) setError(null);
        } else {
          continuePolling = false;
          setStatus(next);
          setError("Сервер доступен, но запуск сборки не подтверждён. Нажмите «Собрать видео» ещё раз.");
          setPhase("review");
        }
      } catch (pollError) {
        if (stopped) return;
        consecutiveFailures += 1;
        if (!isTransientApiError(pollError)) {
          continuePolling = false;
          const failureMessage = messageFromError(pollError);
          setStatus((current) => current ? {
            ...current,
            status: "failed",
            stage: "Связь с проектом потеряна",
            message: failureMessage,
            error: failureMessage,
          } : current);
          setError(failureMessage);
          setPhase("error");
        } else {
          setStatus((current) => current ? {
            ...current,
            message: `Связь с сервером прервалась. Повторяем проверку (попытка ${consecutiveFailures})…`,
          } : current);
          if (consecutiveFailures >= 2) {
            setError("Связь с сервером нестабильна, но сборка могла продолжиться. Статус проверяется автоматически.");
          }
        }
      } finally {
        if (!stopped && continuePolling) {
          const delay = consecutiveFailures > 0
            ? Math.min(1_000 * (2 ** (consecutiveFailures - 1)), 8_000)
            : 1_000;
          schedule(delay);
        }
      }
    }

    void poll();
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [projectId, renderActive]);

  async function uploadProject() {
    if (!images.length || !audio.length) return;
    setPhase("uploading");
    setError(null);
    setStrategyStateUncertain(false);
    setTimeline(null);
    setStatus(null);
    try {
      if (projectId) {
        await deleteProject(projectId).catch(() => undefined);
      }
      setBusyMessage("Создаём изолированную рабочую область…");
      const project = await createProject();
      setProjectId(project.project_id);
      const audioBytes = audio.reduce((sum, file) => sum + file.size, 0);
      const imageBudgetBytes = PROJECT_UPLOAD_SOFT_LIMIT_BYTES - audioBytes;
      setBusyMessage(`Подготавливаем изображения: 0/${images.length}…`);
      const prepared = await prepareImagesForUpload(images, {
        budgetBytes: imageBudgetBytes,
        onProgress: ({ completed, total }) => {
          setBusyMessage(`Подготавливаем изображения: ${completed}/${total}…`);
        },
      });
      setImages(prepared.files);
      setBusyMessage(`Загружаем изображения пакетами: 0/${prepared.files.length}…`);
      await uploadImagesInBatches(project.project_id, prepared.files, {
        onProgress: ({ completedFiles, totalFiles, completedBatches, totalBatches }) => {
          setBusyMessage(
            `Загружаем изображения: ${completedFiles}/${totalFiles} `
            + `(пакет ${completedBatches}/${totalBatches})…`,
          );
        },
      });
      setBusyMessage(
        audio.length > 1
          ? `Склеиваем ${audio.length} аудиодорожки по очереди и анализируем речь…`
          : "Загружаем аудио, распознаём фразы и естественные паузы…",
      );
      await uploadAudio(project.project_id, audio);
      setBusyMessage("Собираем точный план смены сцен…");
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
    if (!projectId || deletingImageId) return;
    const removedFilename = timeline?.items.find((item) => item.image_id === imageId)?.original_filename;
    setError(null);
    setDeletingImageId(imageId);
    try {
      await deleteImage(projectId, imageId);
      setTimeline(await getTimeline(projectId));
      if (removedFilename) {
        setImages((current) => {
          let removed = false;
          return current.filter((file) => {
            if (!removed && file.name === removedFilename) {
              removed = true;
              return false;
            }
            return true;
          });
        });
      }
    } catch (deleteError) {
      setError(messageFromError(deleteError));
    } finally {
      setDeletingImageId(null);
    }
  }

  async function changeSyncStrategy(strategy: SyncStrategy) {
    if (
      !projectId
      || !timeline
      || timeline.sync_strategy === strategy
      || syncStrategyInFlight.current
    ) return;
    const activeProjectId = projectId;
    syncStrategyInFlight.current = true;
    setSyncingStrategy(true);
    setStrategyStateUncertain(false);
    setError(null);
    // The server discards a completed render before rebuilding the timeline.
    // Hide the old result before the request so a lost response cannot leave a stale download link.
    setStatus(null);
    setPhase("review");
    try {
      const rebuilt = await setSyncStrategy(activeProjectId, strategy);
      setTimeline(rebuilt);
      setStrategyStateUncertain(false);
      setPhase("review");
    } catch (strategyError) {
      if (isTransientApiError(strategyError)) {
        try {
          const reconciled = await getTimeline(activeProjectId);
          setTimeline(reconciled);
          setStrategyStateUncertain(false);
          setPhase("review");
          setError(reconciled.sync_strategy === strategy ? null : messageFromError(strategyError));
        } catch (reconciliationError) {
          setStrategyStateUncertain(true);
          setError(
            `Не удалось подтвердить выбранную стратегию: ${messageFromError(reconciliationError)} `
            + "Повторите синхронизацию по звуку перед сборкой.",
          );
        }
      } else {
        setStrategyStateUncertain(false);
        setError(messageFromError(strategyError));
      }
    } finally {
      syncStrategyInFlight.current = false;
      setSyncingStrategy(false);
    }
  }

  function invalidateProjectAfterMediaChange() {
    const previousId = projectId;
    if (!previousId && !timeline && !status) return;
    setProjectId(null);
    setTimeline(null);
    setSettings(DEFAULT_RENDER_SETTINGS);
    setStatus(null);
    setPhase("empty");
    setBusyMessage("");
    setError(null);
    setDeletingImageId(null);
    setRenderStarting(false);
    setSyncingStrategy(false);
    setStrategyStateUncertain(false);
    renderStartInFlight.current = false;
    syncStrategyInFlight.current = false;
    if (previousId) {
      void deleteProject(previousId).catch(() => undefined);
    }
  }

  function changeImages(files: File[]) {
    setImages(files);
    invalidateProjectAfterMediaChange();
  }

  function changeAudio(files: File[]) {
    setAudio(files);
    invalidateProjectAfterMediaChange();
  }

  async function renderVideo() {
    if (
      !projectId
      || !timeline?.is_valid
      || strategyStateUncertain
      || renderStartInFlight.current
    ) return;
    renderStartInFlight.current = true;
    setRenderStarting(true);
    setError(null);
    setStatus(null);
    setDeletingImageId(null);
    setPhase("rendering");
    const requestId = createRenderRequestId();
    try {
      const accepted = await startRender(projectId, { ...settings, request_id: requestId });
      setStatus({
        ...QUEUED_STATUS,
        project_id: projectId,
        message: accepted.message,
        recent_logs: [accepted.message],
      });
      setPhase("rendering");
    } catch (renderError) {
      if (isTransientApiError(renderError)) {
        const reconciliationMessage =
          "Ответ на запуск не получен. Сборка могла начаться — автоматически проверяем её состояние.";
        setStatus({
          ...QUEUED_STATUS,
          project_id: projectId,
          message: reconciliationMessage,
          recent_logs: [reconciliationMessage],
        });
        setError(reconciliationMessage);
        setPhase("rendering");
      } else {
        setError(messageFromError(renderError));
        setPhase("error");
      }
    } finally {
      renderStartInFlight.current = false;
      setRenderStarting(false);
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
    setAudio([]);
    setProjectId(null);
    setTimeline(null);
    setSettings(DEFAULT_RENDER_SETTINGS);
    setStatus(null);
    setPhase("empty");
    setError(null);
    setDeletingImageId(null);
    setRenderStarting(false);
    setSyncingStrategy(false);
    setStrategyStateUncertain(false);
    renderStartInFlight.current = false;
    syncStrategyInFlight.current = false;
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
            <span className="hero-badge">Монтаж без рутины</span>
            <h1>Кадры становятся <em>готовым видео.</em></h1>
            <p>
              Добавьте изображения и озвучку — AutoFrames найдёт окончания фраз и естественные
              паузы, синхронизирует смену сцен и подготовит ролик к публикации.
            </p>
            <div className="hero-benefits" aria-label="Преимущества">
              <span><i aria-hidden="true">✓</i> Синхронизация по речи</span>
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
            {["Файлы", "Синхронизация", "Настройки", "Результат"].map((label, index) => (
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
          onImagesChange={changeImages}
          onAudioChange={changeAudio}
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
            disabled={busy || deletingImageId !== null || strategyStateUncertain}
            onDelete={(imageId) => void removeImage(imageId)}
            onStrategyChange={(strategy) => void changeSyncStrategy(strategy)}
          />
        )}

        {timeline && phase !== "uploading" && (
          <SettingsPanel
            value={settings}
            timeline={timeline}
            disabled={busy || strategyStateUncertain}
            onChange={setSettings}
            onRender={() => void renderVideo()}
          />
        )}

        {renderStarting && !status && (
          <section className="panel loading-state" aria-live="polite">
            <span className="large-spinner" aria-hidden="true" />
            <h2>Отправляем задачу на сборку…</h2>
            <p>Если сервер запускается после паузы, это может занять до минуты.</p>
          </section>
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
              <span>{displayFps(mediaInfo.fps, settings.video.fps)} FPS</span>
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
