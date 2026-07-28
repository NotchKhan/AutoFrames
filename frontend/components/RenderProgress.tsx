"use client";

import type { StatusResponse } from "@/lib/types";


interface RenderProgressProps {
  status: StatusResponse;
  onCancel: () => void;
}


export function RenderProgress({ status, onCancel }: RenderProgressProps) {
  const active = ["queued", "rendering", "cancelling"].includes(status.status);
  return (
    <section className="panel progress-panel" aria-live="polite" aria-labelledby="progress-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Шаг 4</span>
          <h2 id="progress-title">{status.stage}</h2>
        </div>
        <strong className="progress-number">{Math.round(status.progress_percent)}%</strong>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(status.progress_percent)}
      >
        <span style={{ width: `${Math.min(100, Math.max(0, status.progress_percent))}%` }} />
      </div>
      <div className="progress-meta">
        <p>{status.message}</p>
        <span>
          {status.total > 0 ? `Кадр ${status.current} из ${status.total}` : "Подготовка"}
          {` · операций: ${status.completed_operations}`}
        </span>
      </div>
      {status.error && !active && (
        <div className="render-error-detail" role="alert">
          <strong>{status.status === "cancelled" ? "Сборка остановлена" : "Причина ошибки"}</strong>
          <p>{status.error}</p>
        </div>
      )}
      {status.recent_logs.length > 0 && (
        <details className="log-box">
          <summary>Последние действия</summary>
          <ol>
            {status.recent_logs.slice(-8).map((entry, index) => <li key={`${index}-${entry}`}>{entry}</li>)}
          </ol>
        </details>
      )}
      {active && (
        <button type="button" className="secondary-button danger-button" onClick={onCancel} disabled={status.status === "cancelling"}>
          {status.status === "cancelling" ? "Останавливаем…" : "Отменить рендеринг"}
        </button>
      )}
    </section>
  );
}
