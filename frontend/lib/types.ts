export type ProjectStatus =
  | "draft"
  | "ready"
  | "queued"
  | "rendering"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed";

export interface ProjectResponse {
  project_id: string;
  status: ProjectStatus;
  expires_at: string;
}

export interface ValidationIssue {
  message: string;
  filename: string | null;
  critical: boolean;
}

export interface TimelineRow {
  index: number;
  image_id: string;
  original_filename: string;
  parsed_timestamp: string;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  start_formatted: string;
  end_formatted: string;
  duration_formatted: string;
  is_valid: boolean;
  errors: string[];
  warnings: string[];
  boundary_kind: string;
}

export interface TimelineResponse {
  project_id: string;
  timeline_mode: "timestamps" | "audio_pauses";
  detected_pauses: number;
  detected_sentences: number;
  transcription_used: boolean;
  analysis_method: "manual" | "phrases_and_pauses" | "pauses" | "word_boundaries" | "even" | "unavailable";
  analysis_warning: string | null;
  is_valid: boolean;
  items: TimelineRow[];
  issues: ValidationIssue[];
  audio_uploaded: boolean;
  audio_track_count: number;
  audio_duration_ms: number | null;
  audio_duration_formatted: string | null;
  timeline_end_ms: number | null;
  timeline_end_formatted: string | null;
  difference_ms: number | null;
}

export interface VideoSettingsPayload {
  width: number;
  height: number;
  fps: 24 | 25 | 30 | 60;
  scale_mode: "cover" | "fit_blur" | "fit_color";
  background_color: string;
  motion_mode:
    | "none"
    | "zoom_in"
    | "zoom_out"
    | "left_right"
    | "right_left"
    | "top_bottom"
    | "bottom_top"
    | "auto";
  motion_strength: number;
  motion_speed: number;
  alternate_randomly: boolean;
  seed: number;
  transition_mode: "none" | "fade" | "crossfade_safe";
  transition_duration_ms: number;
  preset: "veryfast" | "medium" | "slow";
  crf: number;
}

export interface RenderPayload {
  video: VideoSettingsPayload;
  audio: {
    normalize: boolean;
    fade_in_ms: number;
    fade_out_ms: number;
    volume_percent: number;
  };
  end_mode:
    | "extend_last"
    | "black"
    | "trim_to_timeline"
    | "trim_video"
    | "pad_silence"
    | "error";
  keep_debug_files: boolean;
  preview_start_ms: number;
  preview_end_ms: number | null;
}

export interface StatusResponse {
  project_id: string;
  status: ProjectStatus;
  stage: string;
  progress_percent: number;
  current: number;
  total: number;
  completed_operations: number;
  message: string;
  recent_logs: string[];
  error: string | null;
  result_ready: boolean;
  media_info: Record<string, string | number | boolean | null>;
}

export const DEFAULT_RENDER_SETTINGS: RenderPayload = {
  video: {
    width: 1920,
    height: 1080,
    fps: 30,
    scale_mode: "cover",
    background_color: "#000000",
    motion_mode: "none",
    motion_strength: 0.06,
    motion_speed: 1,
    alternate_randomly: false,
    seed: 42,
    transition_mode: "none",
    transition_duration_ms: 200,
    preset: "medium",
    crf: 20,
  },
  audio: {
    normalize: false,
    fade_in_ms: 0,
    fade_out_ms: 0,
    volume_percent: 100,
  },
  end_mode: "extend_last",
  keep_debug_files: false,
  preview_start_ms: 0,
  preview_end_ms: null,
};
