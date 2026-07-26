import type { ProjectResponse, RenderPayload, StatusResponse, TimelineResponse } from "@/lib/types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...options, cache: "no-store" });
  } catch {
    throw new ApiClientError(
      "Не удалось связаться с backend. Проверьте NEXT_PUBLIC_API_URL и доступность сервера.",
      "network_error",
      0,
    );
  }
  if (!response.ok) {
    let code = "request_failed";
    let message = `Backend вернул ошибку ${response.status}.`;
    try {
      const payload = (await response.json()) as { error?: { code?: string; message?: string } };
      code = payload.error?.code ?? code;
      message = payload.error?.message ?? message;
    } catch {
      // Ответ без JSON всё равно преобразуется в понятную клиентскую ошибку.
    }
    throw new ApiClientError(message, code, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function createProject(): Promise<ProjectResponse> {
  return request<ProjectResponse>("/api/projects", { method: "POST" });
}

export function uploadImages(projectId: string, files: File[]): Promise<unknown> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file, file.name));
  return request(`/api/projects/${projectId}/images`, { method: "POST", body: form });
}

export function uploadAudio(projectId: string, file: File): Promise<unknown> {
  const form = new FormData();
  form.append("file", file, file.name);
  return request(`/api/projects/${projectId}/audio`, { method: "POST", body: form });
}

export function getTimeline(projectId: string): Promise<TimelineResponse> {
  return request<TimelineResponse>(`/api/projects/${projectId}/timeline`);
}

export function deleteImage(projectId: string, imageId: string): Promise<unknown> {
  return request(`/api/projects/${projectId}/images/${encodeURIComponent(imageId)}`, { method: "DELETE" });
}

export function startRender(projectId: string, payload: RenderPayload): Promise<unknown> {
  return request(`/api/projects/${projectId}/render`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function getStatus(projectId: string): Promise<StatusResponse> {
  return request<StatusResponse>(`/api/projects/${projectId}/status`);
}

export function cancelRender(projectId: string): Promise<unknown> {
  return request(`/api/projects/${projectId}/cancel`, { method: "POST" });
}

export function deleteProject(projectId: string): Promise<unknown> {
  return request(`/api/projects/${projectId}`, { method: "DELETE" });
}

export function resultUrl(projectId: string): string {
  return `${API_URL}/api/projects/${projectId}/result`;
}

export function imageUrl(projectId: string, imageId: string): string {
  return `${API_URL}/api/projects/${projectId}/images/${encodeURIComponent(imageId)}`;
}
