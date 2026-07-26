"use client";

import { useRef, useState } from "react";

import { humanFileSize } from "@/lib/time";


interface FileUploadProps {
  images: File[];
  audio: File | null;
  disabled: boolean;
  onImagesChange: (files: File[]) => void;
  onAudioChange: (file: File | null) => void;
  onSubmit: () => void;
}

const IMAGE_EXTENSIONS = ".png,.jpg,.jpeg,.webp,.bmp";
const AUDIO_EXTENSIONS = ".mp3,.wav,.m4a,.aac,.ogg,.flac";


export function FileUpload({
  images,
  audio,
  disabled,
  onImagesChange,
  onAudioChange,
  onSubmit,
}: FileUploadProps) {
  const imageInput = useRef<HTMLInputElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const totalImageSize = images.reduce((sum, file) => sum + file.size, 0);

  function acceptDropped(files: FileList) {
    const selected = Array.from(files).filter((file) =>
      [".png", ".jpg", ".jpeg", ".webp", ".bmp"].some((extension) => file.name.toLowerCase().endsWith(extension)),
    );
    if (selected.length) {
      onImagesChange(selected);
    }
  }

  return (
    <section className="panel upload-panel" aria-labelledby="upload-title">
      <div className="section-heading">
        <div className="section-title-group">
          <span className="section-number" aria-hidden="true">01</span>
          <div>
            <span className="eyebrow">Новый проект</span>
            <h2 id="upload-title">Добавьте материалы</h2>
            <p className="section-description">Выберите изображения с временными метками и одну дорожку озвучки.</p>
          </div>
        </div>
        <span className="format-hint">До 500 кадров</span>
      </div>

      <div className="upload-grid">
        <button
          type="button"
          className={`drop-zone image-drop-zone ${dragging ? "is-dragging" : ""} ${images.length ? "has-files" : ""}`}
          disabled={disabled}
          onClick={() => imageInput.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            acceptDropped(event.dataTransfer.files);
          }}
        >
          <span className="upload-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none"><path d="M4 16.5V6.75A2.75 2.75 0 0 1 6.75 4h10.5A2.75 2.75 0 0 1 20 6.75v10.5A2.75 2.75 0 0 1 17.25 20H8" /><path d="m4 16.5 4.3-4.3a2 2 0 0 1 2.83 0L14 15.07l1.2-1.2a2 2 0 0 1 2.83 0L20 15.84M15.5 9a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z" /><path d="M4 12v8m-4-4h8" /></svg>
          </span>
          <div className="drop-copy">
            <strong>{images.length ? "Кадры добавлены" : "Изображения"}</strong>
            <span>{images.length ? "Нажмите, чтобы заменить выбранные файлы" : "Перетащите сюда или выберите с устройства"}</span>
          </div>
          <small>PNG · JPG · WEBP · BMP</small>
          {images.length > 0 && (
            <span className="selection-summary">
              <i aria-hidden="true">✓</i> {images.length} файлов · {humanFileSize(totalImageSize)}
            </span>
          )}
        </button>
        <input
          ref={imageInput}
          className="visually-hidden"
          type="file"
          accept={IMAGE_EXTENSIONS}
          multiple
          onChange={(event) => onImagesChange(Array.from(event.target.files ?? []))}
        />

        <button
          type="button"
          className={`drop-zone audio-drop-zone ${audio ? "has-files" : ""}`}
          disabled={disabled}
          onClick={() => audioInput.current?.click()}
        >
          <span className="upload-icon audio-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none"><path d="M6 18V8.5m0 0 12-3V15M6 8.5l12-3" /><circle cx="4" cy="18" r="2" /><circle cx="16" cy="15" r="2" /></svg>
          </span>
          <div className="drop-copy">
            <strong>{audio ? "Озвучка добавлена" : "Озвучка"}</strong>
            <span>{audio ? "Нажмите, чтобы выбрать другой файл" : "Добавьте полную аудиодорожку"}</span>
          </div>
          <small>MP3 · WAV · M4A · AAC · OGG · FLAC</small>
          {audio && (
            <span className="selection-summary">
              <i aria-hidden="true">✓</i> {audio.name} · {humanFileSize(audio.size)}
            </span>
          )}
        </button>
        <input
          ref={audioInput}
          className="visually-hidden"
          type="file"
          accept={AUDIO_EXTENSIONS}
          onChange={(event) => onAudioChange(event.target.files?.[0] ?? null)}
        />
      </div>

      <div className="tip-row">
        <span className="tip-example" aria-hidden="true">[1-15]</span>
        <div>
          <strong>Метка означает время окончания кадра</strong>
          <p><code>[1-15]_scene.jpg</code> будет показываться до 01:15. Начало рассчитается автоматически.</p>
        </div>
      </div>

      <div className="panel-actions">
        <p className="muted">Порядок выбора не важен — кадры автоматически встанут на свои места.</p>
        <button
          type="button"
          className="primary-button"
          disabled={disabled || images.length === 0 || audio === null}
          onClick={onSubmit}
        >
          {disabled ? <span className="spinner" aria-hidden="true" /> : null}
          Построить таймлайн
          {!disabled && <span aria-hidden="true">→</span>}
        </button>
      </div>
    </section>
  );
}
