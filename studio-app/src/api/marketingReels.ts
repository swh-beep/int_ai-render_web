import { readApiError } from "./outputs";
import type { MarketingGenerationAspectRatio, MarketingGenerationMode, MarketingVideoQuality, SourceDurationSec } from "../domain/marketing";

const basePath = "/api/marketing/reel-groups";
const globalPromptsPath = "/api/marketing/global-prompts";
const clipPromptsPath = "/api/marketing/clip-prompts";
const audioPromptsPath = "/api/marketing/audio-prompts";

export type MarketingReelClipCreateInput = {
  clientImageId: string;
  sourceImageUrl: string;
  endImageUrl?: string;
  generationMode?: MarketingGenerationMode;
  order: number;
  prompt: string;
  durationSec: SourceDurationSec;
};

export type MarketingReelGroupCreatePayload = {
  globalPrompt: string;
  audioEnabled?: boolean;
  audioPrompt?: string;
  aspectRatio: MarketingGenerationAspectRatio;
  videoQuality: MarketingVideoQuality;
  platform: string;
  tone: string;
  goal: string;
  clips: MarketingReelClipCreateInput[];
};

export type MarketingReelGroupCreateResponse = {
  group_id: string;
  aspect_ratio: MarketingGenerationAspectRatio;
  video_quality: MarketingVideoQuality;
  audio_enabled?: boolean;
  audio_prompt?: string;
  clips: Array<{ clip_id: string; client_image_id: string }>;
};

export type MarketingClipAttemptPayload = {
  attempt_id: string;
  clip_id: string;
  clip_generation_id?: string;
  source_job_id: string;
  source_job_item_index: number;
  prompt: string;
  duration_sec: SourceDurationSec;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  source_video_url?: string;
  download_url?: string;
  error?: string;
};

export type MarketingClipApprovalPayload = {
  attempt_id: string;
};

export type MarketingClipApprovalResponse = {
  group_id: string;
  clip_id: string;
  approved_attempt_id: string;
};

export type MarketingReelAttemptDetail = {
  attempt_id: string;
  clip_id: string;
  clip_generation_id?: string;
  based_on_draft_version?: number;
  source_job_id: string;
  source_job_item_index: number;
  prompt: string;
  duration_sec: SourceDurationSec;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  source_video_url?: string;
  download_url?: string;
  error?: string;
  created_at?: string;
  updated_at?: string;
};

export type MarketingReelClipDetail = {
  clip_id: string;
  client_image_id: string;
  source_image_url: string;
  end_image_url?: string | null;
  generation_mode: MarketingGenerationMode;
  original_order: number;
  current_order: number;
  initial_prompt: string;
  target_duration_sec: SourceDurationSec;
  approved_attempt_id?: string | null;
  deleted_at?: string | null;
  attempts: MarketingReelAttemptDetail[];
};

export type MarketingClipSourceImageUpdatePayload = {
  clips: Array<{
    clip_id: string;
    source_image_url: string;
    end_image_url?: string;
    generation_mode?: MarketingGenerationMode;
  }>;
};

export type MarketingClipSourceImageUpdateResponse = {
  group_id: string;
  clips: Array<{
    clip_id: string;
    source_image_url: string;
    end_image_url?: string;
    generation_mode?: MarketingGenerationMode;
  }>;
};

export type MarketingFinalResultPayload = {
  compile_job_id: string;
  final_video_url: string;
  final_download_url?: string;
  final_title?: string;
  selected_attempt_ids?: string[];
  compile_payload_summary: unknown;
};

export type MarketingReelGroupListItem = {
  group_id: string;
  created_at: string;
  aspect_ratio?: MarketingGenerationAspectRatio;
  video_quality?: MarketingVideoQuality;
  audio_enabled?: boolean;
  audio_prompt?: string;
  final_title?: string;
  final_video_url?: string;
  representative_image_url?: string;
  clip_count: number;
  status: string;
};

export type MarketingReelGroupDetail = {
  group_id: string;
  status: string;
  aspect_ratio?: MarketingGenerationAspectRatio;
  video_quality?: MarketingVideoQuality;
  audio_enabled?: boolean;
  audio_prompt?: string;
  created_at: string;
  updated_at: string;
  final_video_url?: string;
  final_download_url?: string;
  final_title?: string;
  global_prompt: string;
  platform: string;
  tone: string;
  goal: string;
  clips: MarketingReelClipDetail[];
  generations?: MarketingClipGenerationDetail[];
  compositions?: MarketingClipCompositionDetail[];
};

export type MarketingClipGenerationDetail = {
  clip_generation_id: string;
  group_id: string;
  generation_type: "INITIAL" | "REGENERATE" | "PARTIAL";
  status: string;
  source_job_id?: string | null;
  clip_ids: string[];
  error?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type MarketingClipCompositionDetail = {
  clip_composition_id: string;
  group_id: string;
  compile_job_id: string;
  status: string;
  title?: string | null;
  final_video_url: string;
  final_download_url?: string | null;
  selected_attempt_ids: string[];
  compile_payload_summary?: unknown;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type MarketingGroupTitleUpdateResponse = {
  group_id: string;
  final_title: string;
};

export type MarketingGlobalPromptHistoryItem = {
  id: string;
  title?: string | null;
  global_prompt: string;
  created_at: string;
};

export type MarketingClipPromptHistoryItem = {
  id: string;
  title: string;
  prompt: string;
  created_at: string;
};

export type MarketingAudioPromptHistoryItem = MarketingClipPromptHistoryItem;

export type MarketingAudioSettingsPayload = {
  audioEnabled: boolean;
  audioPrompt: string;
};

async function parseJsonResponse<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export async function createMarketingReelGroup(payload: MarketingReelGroupCreatePayload): Promise<MarketingReelGroupCreateResponse> {
  const response = await fetch(basePath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      global_prompt: payload.globalPrompt,
      audio_enabled: Boolean(payload.audioEnabled),
      audio_prompt: payload.audioPrompt ?? "",
      aspect_ratio: payload.aspectRatio,
      video_quality: payload.videoQuality,
      platform: payload.platform,
      tone: payload.tone,
      goal: payload.goal,
      clips: payload.clips.map((clip) => ({
        client_image_id: clip.clientImageId,
        source_image_url: clip.sourceImageUrl,
        end_image_url: clip.endImageUrl,
        generation_mode: clip.generationMode,
        order: clip.order,
        prompt: clip.prompt,
        duration_sec: clip.durationSec,
      })),
    }),
  });
  return parseJsonResponse<MarketingReelGroupCreateResponse>(response, `마케팅 릴스 그룹 생성 실패 (${response.status})`);
}

export async function saveGlobalPrompt(globalPrompt: string): Promise<MarketingGlobalPromptHistoryItem> {
  const response = await fetch(globalPromptsPath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ global_prompt: globalPrompt }),
  });
  return parseJsonResponse<MarketingGlobalPromptHistoryItem>(response, `Global prompt 저장 실패 (${response.status})`);
}

export async function listGlobalPrompts(limit = 30): Promise<MarketingGlobalPromptHistoryItem[]> {
  const response = await fetch(`${globalPromptsPath}?limit=${encodeURIComponent(String(limit))}`, { cache: "no-store" });
  return parseJsonResponse<MarketingGlobalPromptHistoryItem[]>(response, `Global prompt 내역 조회 실패 (${response.status})`);
}

export async function deleteGlobalPrompt(promptId: string): Promise<{ id: string }> {
  const response = await fetch(`${globalPromptsPath}/${encodeURIComponent(promptId)}`, { method: "DELETE" });
  return parseJsonResponse<{ id: string }>(response, `Global prompt 삭제 실패 (${response.status})`);
}

export async function saveClipPrompt(title: string, prompt: string): Promise<MarketingClipPromptHistoryItem> {
  const response = await fetch(clipPromptsPath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, prompt }),
  });
  return parseJsonResponse<MarketingClipPromptHistoryItem>(response, `Clip prompt 저장 실패 (${response.status})`);
}

export async function listClipPrompts(limit = 30): Promise<MarketingClipPromptHistoryItem[]> {
  const response = await fetch(`${clipPromptsPath}?limit=${encodeURIComponent(String(limit))}`, { cache: "no-store" });
  return parseJsonResponse<MarketingClipPromptHistoryItem[]>(response, `Clip prompt 내역 조회 실패 (${response.status})`);
}

export async function deleteClipPrompt(promptId: string): Promise<{ id: string }> {
  const response = await fetch(`${clipPromptsPath}/${encodeURIComponent(promptId)}`, { method: "DELETE" });
  return parseJsonResponse<{ id: string }>(response, `Clip prompt 삭제 실패 (${response.status})`);
}

export async function updateMarketingAudioSettings(
  groupId: string,
  payload: MarketingAudioSettingsPayload,
): Promise<{ group_id: string; audio_enabled: boolean; audio_prompt: string }> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/audio-settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      audio_enabled: payload.audioEnabled,
      audio_prompt: payload.audioPrompt,
    }),
  });
  return parseJsonResponse(response, `음성 설정 저장 실패 (${response.status})`);
}

export async function saveAudioPrompt(title: string, prompt: string): Promise<MarketingAudioPromptHistoryItem> {
  const response = await fetch(audioPromptsPath, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, prompt }),
  });
  return parseJsonResponse<MarketingAudioPromptHistoryItem>(response, `음성 프롬프트 저장 실패 (${response.status})`);
}

export async function listAudioPrompts(limit = 30): Promise<MarketingAudioPromptHistoryItem[]> {
  const response = await fetch(`${audioPromptsPath}?limit=${encodeURIComponent(String(limit))}`, { cache: "no-store" });
  return parseJsonResponse<MarketingAudioPromptHistoryItem[]>(response, `음성 프롬프트 내역 조회 실패 (${response.status})`);
}

export async function deleteAudioPrompt(promptId: string): Promise<{ id: string }> {
  const response = await fetch(`${audioPromptsPath}/${encodeURIComponent(promptId)}`, { method: "DELETE" });
  return parseJsonResponse<{ id: string }>(response, `음성 프롬프트 삭제 실패 (${response.status})`);
}

export async function markMarketingReelGroupFailed(groupId: string): Promise<{ group_id: string }> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/failed`, { method: "PATCH" });
  return parseJsonResponse<{ group_id: string }>(response, `마케팅 릴스 그룹 실패 처리 실패 (${response.status})`);
}

export async function createMarketingClipGeneration(
  groupId: string,
  payload: { generation_type: "INITIAL" | "REGENERATE" | "PARTIAL"; clip_ids: string[]; source_job_id?: string },
): Promise<{ group_id: string; clip_generation_id: string; generation_type: string; status: string; source_job_id?: string; clip_ids: string[] }> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/clip-generations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse(response, `clip generation 저장 실패 (${response.status})`);
}

export async function deleteMarketingReelClip(
  groupId: string,
  clipId: string,
): Promise<{ group_id: string; clip_id: string; deleted_at: string }> {
  const response = await fetch(
    `${basePath}/${encodeURIComponent(groupId)}/clips/${encodeURIComponent(clipId)}/deleted`,
    { method: "PATCH" },
  );
  return parseJsonResponse<{ group_id: string; clip_id: string; deleted_at: string }>(
    response,
    `clip 삭제 실패 (${response.status})`,
  );
}

export async function updateMarketingClipSourceImages(
  groupId: string,
  payload: MarketingClipSourceImageUpdatePayload,
): Promise<MarketingClipSourceImageUpdateResponse> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/clips/source-images`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<MarketingClipSourceImageUpdateResponse>(
    response,
    `source image URL 저장 실패 (${response.status})`,
  );
}

export async function createMarketingClipAttempt(groupId: string, payload: MarketingClipAttemptPayload): Promise<MarketingClipAttemptPayload> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/clip-attempts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<MarketingClipAttemptPayload>(response, `source attempt 저장 실패 (${response.status})`);
}

export async function updateMarketingClipAttempt(
  groupId: string,
  attemptId: string,
  payload: Partial<MarketingClipAttemptPayload>,
): Promise<MarketingClipAttemptPayload> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/clip-attempts/${encodeURIComponent(attemptId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<MarketingClipAttemptPayload>(response, `source attempt 갱신 실패 (${response.status})`);
}

export async function approveMarketingClipAttempt(
  groupId: string,
  clipId: string,
  payload: MarketingClipApprovalPayload,
): Promise<MarketingClipApprovalResponse> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/clips/${encodeURIComponent(clipId)}/approval`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<MarketingClipApprovalResponse>(response, `source attempt 승인 실패 (${response.status})`);
}

export async function patchMarketingFinalResult(groupId: string, payload: MarketingFinalResultPayload): Promise<{ group_id: string }> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/final`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<{ group_id: string }>(response, `최종 결과 저장 실패 (${response.status})`);
}

export async function updateMarketingReelGroupTitle(
  groupId: string,
  finalTitle: string,
): Promise<MarketingGroupTitleUpdateResponse> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}/title`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ final_title: finalTitle }),
  });
  return parseJsonResponse<MarketingGroupTitleUpdateResponse>(response, `히스토리 제목 수정 실패 (${response.status})`);
}

export async function listMarketingReelGroups(limit = 20): Promise<MarketingReelGroupListItem[]> {
  const response = await fetch(`${basePath}?limit=${encodeURIComponent(String(limit))}`, { cache: "no-store" });
  return parseJsonResponse<MarketingReelGroupListItem[]>(response, `마케팅 릴스 히스토리 조회 실패 (${response.status})`);
}

export async function getMarketingReelGroup(groupId: string): Promise<MarketingReelGroupDetail> {
  const response = await fetch(`${basePath}/${encodeURIComponent(groupId)}`, { cache: "no-store" });
  return parseJsonResponse<MarketingReelGroupDetail>(response, `마케팅 릴스 상세 조회 실패 (${response.status})`);
}
