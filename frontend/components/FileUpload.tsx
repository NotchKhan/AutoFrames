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
        <div>
          <span className="eyebrow">Шаг 1</span>
          <h2 id="upload-title">Загрузите исходные файлы</h2>
        </div>
        <span className="format-hint">до 500 кадров</span>
      </div>

      <div className="upload-grid">
        <button
          type="button"
          className={`drop-zone ${dragging ? "is-dragging" : ""}`}
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
          <span className="upload-icon" aria-hidden="true">▧</span>
          <strong>Изображения</strong>
          <span>Перетащите сюда или нажмите для выбора</span>
          <small>PNG, JPG, JPEG, WEBP, BMP</small>
          {images.length > 0 && (
            <span className="selection-summary">
              {images.length} файлов · {humanFileSize(totalImageSize)}
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
          className="drop-zone"
          disabled={disabled}
          onClick={() => audioInput.current?.click()}
        >
          <span className="upload-icon audio-icon" aria-hidden="true">♫</span>
          <strong>Озвучка</strong>
          <span>Один аудиофайл с полной дорожкой</span>
          <small>MP3, WAV, M4A, AAC, OGG, FLAC</small>
          {audio && (
            <span className="selection-summary">
              {audio.name} · {humanFileSize(audio.size)}
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
        <span aria-hidden="true">i</span>
        <p>
          Пример: <code>[1-15]_scene.jpg</code> будет показываться до отметки 01:15.
          Начало программа рассчитает автоматически по предыдущему кадру.
        </p>
      </div>

      <div className="panel-actions">
        <p className="muted">
          Временная метка — точное время окончания кадра. Порядок выбора файлов не важен.
        </p>
        <button
          type="button"
          className="primary-button"
          disabled={disabled || images.length === 0 || audio === null}
          onClick={onSubmit}
        >
          {disabled ? <span className="spinner" aria-hidden="true" /> : null}
          Проверить файлы
        </button>
      </div>
    </section>
  );
}
