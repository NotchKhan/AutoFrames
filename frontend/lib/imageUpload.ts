import {
  ApiClientError,
  isTransientApiError,
  uploadImages,
} from "@/lib/api";


const MIB = 1024 * 1024;
const DEFAULT_IMAGE_BUDGET_BYTES = 96 * MIB;
const MAX_BATCH_BYTES = 8 * MIB;
const MAX_BATCH_FILES = 12;
const MIN_TARGET_FILE_BYTES = 160 * 1024;

export interface ImagePreparationProgress {
  completed: number;
  total: number;
  sourceBytes: number;
  preparedBytes: number;
}

export interface ImageUploadProgress {
  completedFiles: number;
  totalFiles: number;
  completedBatches: number;
  totalBatches: number;
  uploadedBytes: number;
  totalBytes: number;
}

export interface PreparedImages {
  files: File[];
  sourceBytes: number;
  preparedBytes: number;
  optimizedCount: number;
}

interface PrepareOptions {
  budgetBytes?: number;
  onProgress?: (progress: ImagePreparationProgress) => void;
}

interface UploadOptions {
  onProgress?: (progress: ImageUploadProgress) => void;
}

interface EncodeAttempt {
  maxEdge: number;
  quality: number;
}

const ENCODE_ATTEMPTS: EncodeAttempt[] = [
  { maxEdge: 2_560, quality: 0.86 },
  { maxEdge: 2_304, quality: 0.82 },
  { maxEdge: 2_048, quality: 0.78 },
  { maxEdge: 1_792, quality: 0.74 },
  { maxEdge: 1_600, quality: 0.69 },
  { maxEdge: 1_280, quality: 0.62 },
];


function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}


function extensionForMime(mime: string): string {
  return mime === "image/webp" ? ".webp" : ".jpg";
}


function optimizedFilename(
  originalName: string,
  mime: string,
  index: number,
  reservedNames: Set<string>,
): string {
  const stem = originalName.replace(/\.[^.]*$/, "") || `image_${index + 1}`;
  const extension = extensionForMime(mime);
  let candidate = `${stem}${extension}`;
  let suffix = 2;
  while (reservedNames.has(candidate.toLocaleLowerCase())) {
    candidate = `${stem}__${suffix}${extension}`;
    suffix += 1;
  }
  reservedNames.add(candidate.toLocaleLowerCase());
  return candidate;
}


function canvasBlob(canvas: HTMLCanvasElement, mime: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, mime, quality));
}


async function encodeImage(file: File, targetBytes: number): Promise<Blob | null> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return null;
  }

  try {
    let smallest: Blob | null = null;
    for (const attempt of ENCODE_ATTEMPTS) {
      const scale = Math.min(1, attempt.maxEdge / Math.max(bitmap.width, bitmap.height));
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d", { alpha: true });
      if (!context) return smallest;
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = "high";
      context.drawImage(bitmap, 0, 0, width, height);

      let encoded = await canvasBlob(canvas, "image/webp", attempt.quality);
      if (!encoded || encoded.type !== "image/webp") {
        encoded = await canvasBlob(canvas, "image/jpeg", attempt.quality);
      }
      canvas.width = 1;
      canvas.height = 1;
      if (!encoded) continue;
      if (smallest === null || encoded.size < smallest.size) smallest = encoded;
      if (encoded.size <= targetBytes) return encoded;
    }
    return smallest;
  } finally {
    bitmap.close();
  }
}


export async function prepareImagesForUpload(
  files: File[],
  options: PrepareOptions = {},
): Promise<PreparedImages> {
  const budgetBytes = options.budgetBytes ?? DEFAULT_IMAGE_BUDGET_BYTES;
  if (!files.length) {
    return { files: [], sourceBytes: 0, preparedBytes: 0, optimizedCount: 0 };
  }
  const sourceBytes = files.reduce((sum, file) => sum + file.size, 0);
  if (sourceBytes <= budgetBytes) {
    options.onProgress?.({
      completed: files.length,
      total: files.length,
      sourceBytes,
      preparedBytes: sourceBytes,
    });
    return { files: [...files], sourceBytes, preparedBytes: sourceBytes, optimizedCount: 0 };
  }
  if (budgetBytes < MIN_TARGET_FILE_BYTES * files.length) {
    throw new ApiClientError(
      "Для выбранного количества кадров и аудио недостаточно места. Уменьшите аудио или число изображений.",
      "client_project_too_large",
      0,
    );
  }

  let lower = MIN_TARGET_FILE_BYTES;
  let upper = Math.max(...files.map((file) => file.size));
  while (lower < upper) {
    const candidate = Math.ceil((lower + upper) / 2);
    const projected = files.reduce((sum, file) => sum + Math.min(file.size, candidate), 0);
    if (projected <= budgetBytes) lower = candidate;
    else upper = candidate - 1;
  }
  const targetBytes = lower;
  const reservedNames = new Set(files.map((file) => file.name.toLocaleLowerCase()));
  const prepared: File[] = [];
  let preparedBytes = 0;
  let optimizedCount = 0;

  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    let next = file;
    if (file.size > targetBytes) {
      const encoded = await encodeImage(file, targetBytes);
      if (encoded && encoded.size < file.size) {
        reservedNames.delete(file.name.toLocaleLowerCase());
        next = new File(
          [encoded],
          optimizedFilename(file.name, encoded.type, index, reservedNames),
          { type: encoded.type, lastModified: file.lastModified },
        );
        optimizedCount += 1;
      }
    }
    prepared.push(next);
    preparedBytes += next.size;
    options.onProgress?.({
      completed: index + 1,
      total: files.length,
      sourceBytes,
      preparedBytes,
    });
  }

  if (preparedBytes > budgetBytes) {
    throw new ApiClientError(
      "Даже после автоматической подготовки изображения не помещаются в лимит сервера. Уменьшите число кадров или размер аудио.",
      "client_project_too_large",
      0,
    );
  }
  return { files: prepared, sourceBytes, preparedBytes, optimizedCount };
}


function imageBatches(files: File[]): File[][] {
  const batches: File[][] = [];
  let current: File[] = [];
  let currentBytes = 0;
  for (const file of files) {
    if (
      current.length > 0
      && (current.length >= MAX_BATCH_FILES || currentBytes + file.size > MAX_BATCH_BYTES)
    ) {
      batches.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(file);
    currentBytes += file.size;
  }
  if (current.length) batches.push(current);
  return batches;
}


export async function uploadImagesInBatches(
  projectId: string,
  files: File[],
  options: UploadOptions = {},
): Promise<void> {
  const batches = imageBatches(files);
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  let completedFiles = 0;
  let uploadedBytes = 0;

  for (let index = 0; index < batches.length; index += 1) {
    const batch = batches[index];
    const batchId = `images-${projectId.slice(0, 12)}-${String(index + 1).padStart(4, "0")}`;
    let lastError: unknown;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await uploadImages(projectId, batch, batchId);
        lastError = undefined;
        break;
      } catch (error) {
        lastError = error;
        const projectBusy = error instanceof ApiClientError && error.code === "project_busy";
        if ((!isTransientApiError(error) && !projectBusy) || attempt === 3) throw error;
        await delay(attempt === 1 ? 1_200 : 2_500);
      }
    }
    if (lastError !== undefined) throw lastError;
    completedFiles += batch.length;
    uploadedBytes += batch.reduce((sum, file) => sum + file.size, 0);
    options.onProgress?.({
      completedFiles,
      totalFiles: files.length,
      completedBatches: index + 1,
      totalBatches: batches.length,
      uploadedBytes,
      totalBytes,
    });
  }
}
