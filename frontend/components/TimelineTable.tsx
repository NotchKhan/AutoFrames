"use client";

import Image from "next/image";

import { imageUrl } from "@/lib/api";
import type { TimelineResponse } from "@/lib/types";


interface TimelineTableProps {
  projectId: string;
  timeline: TimelineResponse;
  disabled: boolean;
  onDelete: (imageId: string) => void;
}


export function TimelineTable({ projectId, timeline, disabled, onDelete }: TimelineTableProps) {
  return (
    <section className="panel timeline-panel" aria-labelledby="timeline-title">
      <div className="section-heading timeline-heading">
        <div>
          <span className="eyebrow">Шаг 2</span>
          <h2 id="timeline-title">Проверка таймлайна</h2>
        </div>
        <span className={`status-pill ${timeline.is_valid ? "success" : "danger"}`}>
          {timeline.is_valid ? "Таймлайн корректен" : `${timeline.issues.length} ошибок`}
        </span>
      </div>

      <div className="metric-grid">
        <div className="metric-card">
          <span>Кадров</span>
          <strong>{timeline.items.length}</strong>
        </div>
        <div className="metric-card">
          <span>Окончание кадров</span>
          <strong>{timeline.timeline_end_formatted ?? "—"}</strong>
        </div>
        <div className="metric-card">
          <span>Длительность аудио</span>
          <strong>{timeline.audio_duration_formatted ?? "—"}</strong>
        </div>
        <div className="metric-card">
          <span>Разница</span>
          <strong className={Math.abs(timeline.difference_ms ?? 0) <= 50 ? "positive" : "attention"}>
            {timeline.difference_ms === null
              ? "—"
              : `${timeline.difference_ms >= 0 ? "+" : ""}${(timeline.difference_ms / 1000).toFixed(3)} с`}
          </strong>
        </div>
      </div>

      {timeline.issues.length > 0 && (
        <div className="issue-list" role="alert">
          {timeline.issues.map((issue, index) => (
            <div className="issue" key={`${issue.filename ?? "project"}-${index}`}>
              <span aria-hidden="true">!</span>
              <p>{issue.message}</p>
            </div>
          ))}
        </div>
      )}

      {timeline.items.length === 0 ? (
        <div className="empty-state">
          <span aria-hidden="true">□</span>
          <h3>Корректных кадров пока нет</h3>
          <p>Проверьте формат временных меток и загрузите файлы снова.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>№</th>
                <th>Кадр</th>
                <th>Исходное имя</th>
                <th>Начало</th>
                <th>Окончание</th>
                <th>Длительность</th>
                <th>Статус</th>
                <th><span className="visually-hidden">Действия</span></th>
              </tr>
            </thead>
            <tbody>
              {timeline.items.map((item) => (
                <tr key={item.image_id}>
                  <td className="row-index">{item.index}</td>
                  <td>
                    <Image
                      className="timeline-thumb"
                      src={imageUrl(projectId, item.image_id)}
                      alt=""
                      width={72}
                      height={44}
                      unoptimized
                    />
                  </td>
                  <td className="filename-cell" title={item.original_filename}>{item.original_filename}</td>
                  <td className="time-cell">{item.start_formatted}</td>
                  <td className="time-cell emphasis">{item.end_formatted}</td>
                  <td className="time-cell">{item.duration_formatted}</td>
                  <td>
                    <span className={`row-status ${item.is_valid ? "valid" : "invalid"}`}>
                      {item.is_valid ? "Готов" : "Ошибка"}
                    </span>
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      type="button"
                      disabled={disabled}
                      aria-label={`Удалить ${item.original_filename}`}
                      title="Удалить кадр"
                      onClick={() => onDelete(item.image_id)}
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
