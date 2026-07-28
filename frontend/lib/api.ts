import type {
  ProjectResponse,
  RenderAcceptedResponse,
  RenderStartPayload,
  StatusResponse,
  SyncStrategy,
  TimelineResponse,
} from "@/lib/types";

const CONFIGURED_API_URL = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/+$/, "") ?? "";

export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

function apiUrl(): string {
  const browserIsLocal = typeof window !== "undefined" && isLoopbackHost(window.location.hostname);
  const candidate =
    CONFIGURED_API_URL ||
    (browserIsLocal ? `${window.location.protocol}//${window.location.hostname}:8000` : "");

  if (!candidate) {
    throw new ApiClientError(
      "Backend не настроен: добавьте NEXT_PUBLIC_API_URL в настройках frontend и выполните новый deployment.",
      "api_url_missing",
      0,
    );
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new ApiClientError(
      "NEXT_PUBLIC_API_URL содержит некорректный адрес backend.",
      "api_url_invalid",
      0,
    );
  }

  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    throw new ApiClientError(
      "NEXT_PUBLIC_API_URL должен начинаться с http:// или https://.",
      "api_url_invalid",
      0,
    );
  }

  if (typeof window !== "undefined") {
    if (!browserIsLocal && isLoopbackHost(parsed.hostname)) {
      throw new ApiClientError(
        "Frontend опубликован с локальным адресом backend. Укажите публичный HTTPS URL в NEXT_PUBLIC_API_URL и выполните Redeploy.",
        "api_url_localhost",
        0,
      );
    }
    if (window.location.protocol === "https:" && parsed.protocol !== "https:") {
      throw new ApiClientError(
        "HTTPS-страница не может обращаться к backend по HTTP. Укажите HTTPS URL в NEXT_PUBLIC_API_URL.",
        "api_url_insecure",
        0,
      );
    }
  }

  return parsed.toString().replace(/\/$/, "");
}

interface ApiRequestOptions extends RequestInit {
  timeoutMs?: number;
}

const FALLBACK_MESSAGES: Record<number, string> = {
  408: "Сервер не успел обработать запрос. Попробуйте ещё раз.",
  413: "Выбранные файлы слишком велики для сервера.",
  429: "Сервер временно перегружен запросами. Подождите немного и повторите.",
  500: "На сервере произошла внутренняя ошибка.",
  502: "Сервер обработки временно недоступен.",
  503: "Сервис обработки временно недоступен.",
  504: "Сервер обработки не успел ответить.",
};

async function request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const baseUrl = apiUrl();
  const { timeoutMs = 45_000, signal: callerSignal, ...fetchOptions } = options;
  const controller = new AbortController();
  let timedOut = false;
  const forwardAbort = () => controller.abort();
  if (callerSignal?.aborted) controller.abort();
  else callerSignal?.addEventListener("abort", forwardAbort, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  let response: Response;
  let rawBody: string;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...fetchOptions,
      signal: controller.signal,
      cache: "no-store",
    });
    rawBody = response.status === 204 ? "" : await response.text();
  } catch {
    if (timedOut) {
      throw new ApiClientError(
        "Сервер слишком долго не отвечает. Проверьте состояние операции перед повтором.",
        "request_timeout",
        0,
      );
    }
    if (callerSignal?.aborted) {
      throw new ApiClientError("Запрос отменён.", "request_cancelled", 0);
    }
    throw new ApiClientError(
      "Не удалось связаться с сервером обработки. Проверьте интернет или подождите, пока сервис запустится после паузы.",
      "network_error",
      0,
    );
  } finally {
    clearTimeout(timeout);
    callerSignal?.removeEventListener("abort", forwardAbort);
  }

  if (!response.ok) {
    let code = "request_failed";
    let message = FALLBACK_MESSAGES[response.status] ?? `Сервер вернул ошибку ${response.status}.`;
    try {
      const payload = JSON.parse(rawBody) as {
        error?: { code?: string; message?: string };
        detail?: string;
      };
      code = payload.error?.code ?? code;
      message = payload.error?.message ?? payload.detail ?? message;
    } catch {
      // HTML и пустые ответы прокси не показываем пользователю как технический текст.
    }
    throw new ApiClientError(message, code, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  try {
    return JSON.parse(rawBody) as T;
  } catch {
    throw new ApiClientError(
      "Сервер вернул повреждённый ответ. Повторите операцию.",
      "invalid_response",
      response.status,
    );
  }
}

export function isTransientApiError(error: unknown): boolean {
  if (!(error instanceof ApiClientError)) return false;
  if (["network_error", "request_timeout", "invalid_response"].includes(error.code)) return true;
  return [408, 429, 502, 504].includes(error.status)
    || (error.status === 500 && ["request_failed", "http_500"].includes(error.code))
    || (error.status === 503 && ["request_failed", "http_503"].includes(error.code));
}

function retryDelay(attempt: number): Promise<void> {
  const delayMs = attempt === 1 ? 800 : 2_000;
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

export function createProject(): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/projects", { method: "POST", timeoutMs: 30_000 });
}

export function uploadImages(projectId: string, files: File[]): Promise<unknown> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file, file.name));
  return request(`/api/projects/${projectId}/images`, {
    method: "POST",
    body: form,
    timeoutMs: 180_000,
  });
}

export function uploadAudio(projectId: string, files: File[]): Promise<unknown> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file, file.name));
  return request(`/api/projects/${projectId}/audio`, {
    method: "POST",
    body: form,
    timeoutMs: 600_000,
  });
}

export function getTimeline(projectId: string): Promise<TimelineResponse> {
  return request<TimelineResponse>(`/api/projects/${projectId}/timeline`);
}

export async function setSyncStrategy(
  projectId: string,
  strategy: SyncStrategy,
): Promise<TimelineResponse> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      return await request<TimelineResponse>(`/api/projects/${projectId}/sync-strategy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy }),
        timeoutMs: 45_000,
      });
    } catch (error) {
      lastError = error;
      if (!isTransientApiError(error) || attempt === 3) throw error;
      await retryDelay(attempt);
    }
  }
  throw lastError;
}

export function deleteImage(projectId: string, imageId: string): Promise<unknown> {
  return request(`/api/projects/${projectId}/images/${encodeURIComponent(imageId)}`, { method: "DELETE" });
}

export async function startRender(
  projectId: string,
  payload: RenderStartPayload,
): Promise<RenderAcceptedResponse> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      return await request<RenderAcceptedResponse>(`/api/projects/${projectId}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        timeoutMs: 35_000,
      });
    } catch (error) {
      lastError = error;
      if (!isTransientApiError(error) || attempt === 3) throw error;
      await retryDelay(attempt);
    }
  }
  throw lastError;
}

export function getStatus(projectId: string): Promise<StatusResponse> {
  return request<StatusResponse>(`/api/projects/${projectId}/status`, { timeoutMs: 15_000 });
}

export function cancelRender(projectId: string): Promise<unknown> {
  return request(`/api/projects/${projectId}/cancel`, { method: "POST" });
}

export function deleteProject(projectId: string): Promise<unknown> {
  return request(`/api/projects/${projectId}`, { method: "DELETE" });
}

export function resultUrl(projectId: string): string {
  return `${apiUrl()}/api/projects/${projectId}/result`;
}

export function imageUrl(projectId: string, imageId: string): string {
  return `${apiUrl()}/api/projects/${projectId}/images/${encodeURIComponent(imageId)}`;
}
