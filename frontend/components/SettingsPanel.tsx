"use client";

import type { RenderPayload, TimelineResponse, VideoSettingsPayload } from "@/lib/types";


interface SettingsPanelProps {
  value: RenderPayload;
  timeline: TimelineResponse;
  disabled: boolean;
  onChange: (value: RenderPayload) => void;
  onRender: () => void;
}

const RESOLUTIONS = {
  youtube: [1920, 1080],
  vertical: [1080, 1920],
  square: [1080, 1080],
} as const;


export function SettingsPanel({ value, timeline, disabled, onChange, onRender }: SettingsPanelProps) {
  const video = value.video;
  const resolution = Object.entries(RESOLUTIONS).find(([, size]) => size[0] === video.width && size[1] === video.height)?.[0] ?? "custom";

  function updateVideo(patch: Partial<VideoSettingsPayload>) {
    onChange({ ...value, video: { ...value.video, ...patch } });
  }

  function updateAudio(patch: Partial<RenderPayload["audio"]>) {
    onChange({ ...value, audio: { ...value.audio, ...patch } });
  }

  function applyQuality(profile: string) {
    const quality = {
      fast: { preset: "veryfast" as const, crf: 23 },
      balanced: { preset: "medium" as const, crf: 20 },
      high: { preset: "slow" as const, crf: 18 },
    }[profile];
    if (quality) updateVideo(quality);
  }

  const quality = video.preset === "veryfast" && video.crf === 23
    ? "fast"
    : video.preset === "slow" && video.crf === 18
      ? "high"
      : "balanced";

  return (
    <section className="panel settings-panel" aria-labelledby="settings-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Шаг 3</span>
          <h2 id="settings-title">Настройки видео</h2>
        </div>
        <span className="format-hint">Гибкие параметры</span>
      </div>

      <div className="settings-groups">
        <fieldset disabled={disabled}>
          <legend>Формат и качество</legend>
          <div className="form-grid three-columns">
            <label>
              Формат кадра
              <select
                value={resolution}
                onChange={(event) => {
                  const selected = RESOLUTIONS[event.target.value as keyof typeof RESOLUTIONS];
                  if (selected) {
                    updateVideo({ width: selected[0], height: selected[1] });
                  } else {
                    updateVideo({ width: 1280, height: 720 });
                  }
                }}
              >
                <option value="youtube">YouTube 16:9 — 1920×1080</option>
                <option value="vertical">Shorts / Reels 9:16 — 1080×1920</option>
                <option value="square">Квадрат 1:1 — 1080×1080</option>
                <option value="custom">Пользовательский</option>
              </select>
            </label>
            <label>
              FPS
              <select value={video.fps} onChange={(event) => updateVideo({ fps: Number(event.target.value) as 24 | 25 | 30 | 60 })}>
                {[24, 25, 30, 60].map((fps) => <option key={fps} value={fps}>{fps} кадров/с</option>)}
              </select>
            </label>
            <label>
              Качество
              <select value={quality} onChange={(event) => applyQuality(event.target.value)}>
                <option value="fast">Быстрое · CRF 23</option>
                <option value="balanced">Сбалансированное · CRF 20</option>
                <option value="high">Высокое · CRF 18</option>
              </select>
            </label>
          </div>
          {resolution === "custom" && (
            <div className="form-grid two-columns compact-row">
              <label>
                Ширина
                <input type="number" min="2" max="8192" step="2" value={video.width} onChange={(event) => updateVideo({ width: Number(event.target.value) })} />
              </label>
              <label>
                Высота
                <input type="number" min="2" max="8192" step="2" value={video.height} onChange={(event) => updateVideo({ height: Number(event.target.value) })} />
              </label>
            </div>
          )}
        </fieldset>

        <fieldset disabled={disabled}>
          <legend>Обработка кадров</legend>
          <div className="form-grid three-columns">
            <label>
              Масштабирование
              <select value={video.scale_mode} onChange={(event) => updateVideo({ scale_mode: event.target.value as VideoSettingsPayload["scale_mode"] })}>
                <option value="cover">Заполнить с обрезкой</option>
                <option value="fit_blur">Вписать + размытый фон</option>
                <option value="fit_color">Вписать + цветной фон</option>
              </select>
            </label>
            <label>
              Движение
              <select value={video.motion_mode} onChange={(event) => updateVideo({ motion_mode: event.target.value as VideoSettingsPayload["motion_mode"] })}>
                <option value="none">Без движения</option>
                <option value="zoom_in">Медленное приближение</option>
                <option value="zoom_out">Медленное отдаление</option>
                <option value="left_right">Слева направо</option>
                <option value="right_left">Справа налево</option>
                <option value="top_bottom">Сверху вниз</option>
                <option value="bottom_top">Снизу вверх</option>
                <option value="auto">Авточередование</option>
              </select>
            </label>
            <label>
              Переход
              <select value={video.transition_mode} onChange={(event) => updateVideo({ transition_mode: event.target.value as VideoSettingsPayload["transition_mode"] })}>
                <option value="none">Без перехода</option>
                <option value="fade">Fade внутри кадра</option>
                <option value="crossfade_safe">Безопасный crossfade</option>
              </select>
            </label>
          </div>
          <div className="form-grid three-columns compact-row">
            {video.scale_mode === "fit_color" ? (
              <label>
                Цвет фона
                <input type="color" value={video.background_color} onChange={(event) => updateVideo({ background_color: event.target.value })} />
              </label>
            ) : <span />}
            {video.motion_mode !== "none" ? (
              <label>
                Сила движения · {Math.round(video.motion_strength * 100)}%
                <input type="range" min="0.01" max="0.35" step="0.01" value={video.motion_strength} onChange={(event) => updateVideo({ motion_strength: Number(event.target.value) })} />
              </label>
            ) : <span />}
            {video.transition_mode !== "none" ? (
              <label>
                Переход · {(video.transition_duration_ms / 1000).toFixed(1)} с
                <input type="range" min="0" max="2000" step="100" value={video.transition_duration_ms} onChange={(event) => updateVideo({ transition_duration_ms: Number(event.target.value) })} />
              </label>
            ) : <span />}
          </div>
        </fieldset>

        <fieldset disabled={disabled}>
          <legend>Аудио и окончание ролика</legend>
          <div className="form-grid three-columns">
            <label>
              Поведение в конце
              <select value={value.end_mode} onChange={(event) => onChange({ ...value, end_mode: event.target.value as RenderPayload["end_mode"] })}>
                {timeline.difference_ms !== null && timeline.difference_ms > 50 ? (
                  <>
                    <option value="extend_last">Продлить последний кадр</option>
                    <option value="black">Добавить чёрный экран</option>
                    <option value="trim_to_timeline">Обрезать по таймлайну</option>
                  </>
                ) : timeline.difference_ms !== null && timeline.difference_ms < -50 ? (
                  <>
                    <option value="trim_video">Обрезать по аудио</option>
                    <option value="pad_silence">Добавить тишину</option>
                    <option value="error">Требовать исправления</option>
                  </>
                ) : (
                  <option value="extend_last">Длительности совпадают</option>
                )}
              </select>
            </label>
            <label>
              Громкость · {value.audio.volume_percent}%
              <input type="range" min="0" max="200" step="5" value={value.audio.volume_percent} onChange={(event) => updateAudio({ volume_percent: Number(event.target.value) })} />
            </label>
            <label className="checkbox-label">
              <input type="checkbox" checked={value.audio.normalize} onChange={(event) => updateAudio({ normalize: event.target.checked })} />
              Нормализовать громкость
            </label>
          </div>
          <div className="form-grid two-columns compact-row">
            <label>
              Плавное появление, мс
              <input type="number" min="0" step="100" value={value.audio.fade_in_ms} onChange={(event) => updateAudio({ fade_in_ms: Number(event.target.value) })} />
            </label>
            <label>
              Плавное исчезновение, мс
              <input type="number" min="0" step="100" value={value.audio.fade_out_ms} onChange={(event) => updateAudio({ fade_out_ms: Number(event.target.value) })} />
            </label>
          </div>
        </fieldset>
      </div>

      <div className="panel-actions render-actions">
        <p className="muted">После запуска вы сможете следить за каждым этапом обработки.</p>
        <button type="button" className="primary-button render-button" disabled={disabled || !timeline.is_valid} onClick={onRender}>
          Запустить рендеринг
          <span aria-hidden="true">→</span>
        </button>
      </div>
    </section>
  );
}
