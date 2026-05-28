import { readApiError } from "./outputs";
import type { CompilePayload as AssembleCompilePayload, SourceClipPayload } from "../domain/videoStudio";
import type { MarketingGenerationAspectRatio, MarketingVideoQuality } from "../domain/marketing";

export type MarketingSourceGenerationPayload = {
  items: Array<{
    url: string;
    end_url?: string;
    motion: string;
    effect: string;
    custom_motion_prompt: string | null;
    custom_effect_prompt: string | null;
    duration?: string;
  }>;
  cfg_scale: number;
  aspect_ratio: MarketingGenerationAspectRatio;
  video_quality: MarketingVideoQuality;
  sound?: "off" | "on";
};

export type MarketingCompilePayload = {
  clips: Array<{
    video_url: string;
    speed: number;
    trim_start: number;
    trim_end: number;
    reverse: boolean;
    flip_horizontal: boolean;
  }>;
  aspect_ratio: string;
  video_quality?: MarketingVideoQuality;
  preserve_audio?: boolean;
  aspect_mode: string;
};

export type VideoJobState = {
  status?: string;
  message?: string;
  progress?: number;
  error?: string;
  result_url?: string;
  results?: Array<string | null>;
  errors?: Array<{ index: number; error?: string }>;
};

export async function requestSourceGeneration(payload: SourceClipPayload | MarketingSourceGenerationPayload): Promise<string> {
  const response = await fetch("/video-mvp/generate-sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readApiError(response, `Kling 생성 요청 실패 (${response.status})`));

  const body = (await response.json()) as { job_id?: string };
  if (!body.job_id) throw new Error("Kling 생성 job id가 없습니다.");
  return body.job_id;
}

export async function requestMarketingSourceGeneration(payload: MarketingSourceGenerationPayload): Promise<string> {
  const response = await fetch("/video-mvp/generate-sources-local", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readApiError(response, `Kling 생성 요청 실패 (${response.status})`));

  const body = (await response.json()) as { job_id?: string };
  if (!body.job_id) throw new Error("Kling 생성 job id가 없습니다.");
  return body.job_id;
}

export async function requestCompile(payload: AssembleCompilePayload | MarketingCompilePayload): Promise<string> {
  const response = await fetch("/video-mvp/compile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readApiError(response, `최종 릴스 컴파일 실패 (${response.status})`));

  const body = (await response.json()) as { job_id?: string };
  if (!body.job_id) throw new Error("컴파일 job id가 없습니다.");
  return body.job_id;
}

export async function requestMarketingCompile(payload: MarketingCompilePayload): Promise<string> {
  const response = await fetch("/video-mvp/compile-local", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await readApiError(response, `최종 릴스 컴파일 실패 (${response.status})`));

  const body = (await response.json()) as { job_id?: string };
  if (!body.job_id) throw new Error("컴파일 job id가 없습니다.");
  return body.job_id;
}

export async function fetchVideoJobStatus(jobId: string): Promise<VideoJobState> {
  const response = await fetch(`/video-mvp/status/${jobId}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`상태 확인 실패 (${response.status})`);
  return response.json() as Promise<VideoJobState>;
}

export function downloadUrlForResult(url: string): string {
  return `/download?url=${encodeURIComponent(url)}`;
}
