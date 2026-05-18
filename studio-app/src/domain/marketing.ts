export type MarketingGenerationMode = "START_ONLY" | "START_END" | "NEXT_START_AS_END";
export type MarketingGenerationAspectRatio = "9:16" | "16:9";
export type MarketingAspectRatio = MarketingGenerationAspectRatio | "source";
export type MarketingVideoQuality = "720p" | "1080p";

export type SourceGenerationPayload = {
  items: Array<{
    url: string;
    end_url?: string;
    motion: "custom";
    effect: "none";
    custom_motion_prompt: string;
    custom_effect_prompt: null;
    duration: SourceDurationString;
  }>;
  cfg_scale: number;
  aspect_ratio: MarketingGenerationAspectRatio;
  video_quality: MarketingVideoQuality;
  sound: "off" | "on";
};

export type CompilePayload = {
  clips: Array<{
    video_url: string;
    speed: number;
    trim_start: number;
    trim_end: number;
    reverse: boolean;
    flip_horizontal: boolean;
  }>;
  include_intro_outro: false;
  aspect_ratio: MarketingGenerationAspectRatio;
  video_quality: MarketingVideoQuality;
  preserve_audio: boolean;
  aspect_mode: "crop";
};

export type MarketingBrief = {
  imageUrls: string[];
  endImageUrls?: Array<string | undefined>;
  cutPrompts: string[];
  targetDurationsSec?: SourceDurationSec[];
  globalPrompt: string;
  audioEnabled?: boolean;
  audioPrompt?: string;
  language: string;
  aspectRatio?: MarketingAspectRatio;
  videoQuality?: MarketingVideoQuality;
};

export type SourceDurationSec = 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10;
export type SourceDurationString = "3" | "4" | "5" | "6" | "7" | "8" | "9" | "10";

export type MarketingVideoAttemptStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";

export type MarketingVideoAttempt = {
  attemptId: string;
  sourceJobId: string;
  index: number;
  prompt: string;
  videoUrl?: string;
  downloadUrl?: string;
  status: MarketingVideoAttemptStatus;
  durationSec: SourceDurationSec;
  error?: string;
  createdAt: string;
};

export type MarketingImageItem = {
  clientImageId: string;
  file: File;
  uploadedUrl?: string;
  sourceImageUrl?: string;
  endFile?: File;
  endPreviewUrl?: string;
  endUploadedUrl?: string;
  endImageUrl?: string;
  generationMode?: MarketingGenerationMode;
  order: number;
  prompt: string;
  targetDurationSec: SourceDurationSec;
  attempts: MarketingVideoAttempt[];
  approvedAttemptId?: string;
  isDeleted?: boolean;
};

export const minimumImageCount = 1;
export const maximumImageCount = 10;
export const defaultSourceDurationSec: SourceDurationSec = 5;
export const allowedSourceDurationsSec: SourceDurationSec[] = [3, 4, 5, 6, 7, 8, 9, 10];
export const defaultMarketingAspectRatio: MarketingGenerationAspectRatio = "9:16";
export const allowedMarketingAspectRatios: MarketingGenerationAspectRatio[] = ["9:16", "16:9"];
export const allowedMarketingAspectRatioOptions: MarketingAspectRatio[] = ["source", "9:16", "16:9"];
export const defaultMarketingVideoQuality: MarketingVideoQuality = "720p";
export const allowedMarketingVideoQualities: MarketingVideoQuality[] = ["720p", "1080p"];

export const defaultCutPrompts = ["오프닝 - 정면 클로즈업", "와이드 컷 - 공간감 강조", "디테일 텍스처 컷"];

export function validateImageSelection(files: File[]): File[] {
  if (files.length < minimumImageCount || files.length > maximumImageCount) {
    throw new Error("이미지는 1~10장을 선택해야 합니다.");
  }
  if (files.some((file) => !file.type.startsWith("image/"))) {
    throw new Error("이미지 파일만 업로드할 수 있습니다.");
  }
  return files;
}

export function isSourceDurationSec(value: number): value is SourceDurationSec {
  return allowedSourceDurationsSec.includes(value as SourceDurationSec);
}

export function normalizeSourceDurationSec(value: unknown): SourceDurationSec {
  const numeric = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  return isSourceDurationSec(numeric) ? numeric : defaultSourceDurationSec;
}

export function sourceDurationToPayload(value: SourceDurationSec): SourceDurationString {
  return String(value) as SourceDurationString;
}

export function normalizeMarketingAspectRatio(value: unknown): MarketingAspectRatio {
  return allowedMarketingAspectRatioOptions.includes(value as MarketingAspectRatio)
    ? value as MarketingAspectRatio
    : defaultMarketingAspectRatio;
}

export function normalizeGenerationAspectRatio(value: unknown): MarketingGenerationAspectRatio {
  return allowedMarketingAspectRatios.includes(value as MarketingGenerationAspectRatio)
    ? value as MarketingGenerationAspectRatio
    : defaultMarketingAspectRatio;
}

export function normalizeMarketingVideoQuality(value: unknown): MarketingVideoQuality {
  return allowedMarketingVideoQualities.includes(value as MarketingVideoQuality)
    ? value as MarketingVideoQuality
    : defaultMarketingVideoQuality;
}

export function requiresEndFrame(mode: MarketingGenerationMode | undefined): boolean {
  return mode === "START_END" || mode === "NEXT_START_AS_END";
}

export function generationModeLabel(mode: MarketingGenerationMode | undefined): string {
  if (mode === "NEXT_START_AS_END") return "Next Start as End";
  if (mode === "START_END") return "Start/End";
  return "Start only";
}

export function moveImage<T>(items: T[], index: number, direction: -1 | 1): T[] {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= items.length) return items.slice();
  const next = items.slice();
  const [item] = next.splice(index, 1);
  next.splice(nextIndex, 0, item);
  return next;
}

export function removeImage<T>(items: T[], index: number): T[] {
  return items.filter((_, itemIndex) => itemIndex !== index);
}

export function addCutPrompt(prompts: string[]): string[] {
  return [...prompts, "추가 컷 - 제품과 공간 연결"];
}

export function removeCutPrompt(prompts: string[], index: number): string[] {
  if (prompts.length <= 3) return prompts.slice();
  return prompts.filter((_, promptIndex) => promptIndex !== index);
}

export function buildKlingPrompt(brief: Omit<MarketingBrief, "imageUrls">, cutPrompt: string, index: number): string {
  const audioPrompt = brief.audioEnabled && brief.audioPrompt?.trim()
    ? `Audio: ${brief.audioPrompt.trim()}`
    : "";
  return [
    `cut ${index + 1}: ${cutPrompt}`,
    `global direction: ${brief.globalPrompt}`,
    `language: ${brief.language}`,
    audioPrompt,
    "Keep the furniture, room layout, product shape, material, color, and perspective faithful to the source photo. Smooth professional marketing reel motion. No text overlays.",
  ]
    .filter(Boolean)
    .join(". ")
    .slice(0, 2400);
}

export function buildSourceGenerationPayload(brief: MarketingBrief): SourceGenerationPayload {
  const safePrompts = brief.cutPrompts.filter(Boolean);
  return {
    items: brief.imageUrls.map((url, index) => ({
      url,
      ...(brief.endImageUrls?.[index] ? { end_url: brief.endImageUrls[index] } : {}),
      motion: "custom",
      effect: "none",
      custom_motion_prompt: buildKlingPrompt(
        brief,
        safePrompts[index] ?? safePrompts[safePrompts.length - 1] ?? "premium furniture reel",
        index,
      ),
      custom_effect_prompt: null,
      duration: sourceDurationToPayload(normalizeSourceDurationSec(brief.targetDurationsSec?.[index])),
    })),
    cfg_scale: 0.5,
    aspect_ratio: normalizeGenerationAspectRatio(brief.aspectRatio),
    video_quality: normalizeMarketingVideoQuality(brief.videoQuality),
    sound: brief.audioEnabled ? "on" : "off",
  };
}

export function getApprovedAttemptsInOrder(items: MarketingImageItem[]): MarketingVideoAttempt[] {
  return items
    .filter((item) => !item.isDeleted)
    .slice()
    .sort((left, right) => left.order - right.order)
    .map((item) => item.attempts.find((attempt) => attempt.attemptId === item.approvedAttemptId))
    .filter((attempt): attempt is MarketingVideoAttempt => {
      if (!attempt) return false;
      return attempt.status === "COMPLETED" && Boolean(attempt.videoUrl);
    });
}

export function getCompileBlockers(items: MarketingImageItem[]): MarketingImageItem[] {
  return items
    .filter((item) => !item.isDeleted)
    .filter((item) => {
      const approved = item.attempts.find((attempt) => attempt.attemptId === item.approvedAttemptId);
      return !approved || approved.status !== "COMPLETED" || !approved.videoUrl;
    });
}

export function buildCompilePayloadFromApprovedItems(
  items: MarketingImageItem[],
  aspectRatio: MarketingGenerationAspectRatio = defaultMarketingAspectRatio,
  videoQuality: MarketingVideoQuality = defaultMarketingVideoQuality,
  preserveAudio = false,
): CompilePayload {
  const approvedAttempts = getApprovedAttemptsInOrder(items);
  return {
    clips: approvedAttempts.map((attempt) => ({
      video_url: attempt.videoUrl as string,
      speed: 1,
      trim_start: 0,
      trim_end: attempt.durationSec,
      reverse: false,
      flip_horizontal: false,
    })),
    include_intro_outro: false,
    aspect_ratio: normalizeGenerationAspectRatio(aspectRatio),
    video_quality: normalizeMarketingVideoQuality(videoQuality),
    preserve_audio: preserveAudio,
    aspect_mode: "crop",
  };
}

export function buildCompilePayload(
  sourceUrls: string[],
  targetDurationSec: number,
  aspectRatio: MarketingGenerationAspectRatio = defaultMarketingAspectRatio,
  videoQuality: MarketingVideoQuality = defaultMarketingVideoQuality,
): CompilePayload {
  const clips: CompilePayload["clips"] = [];
  let remaining = targetDurationSec;
  let index = 0;

  while (remaining > 0 && sourceUrls.length > 0) {
    const trimEnd = Math.min(5, remaining);
    clips.push({
      video_url: sourceUrls[index % sourceUrls.length],
      speed: 1,
      trim_start: 0,
      trim_end: trimEnd,
      reverse: false,
      flip_horizontal: false,
    });
    remaining -= trimEnd;
    index += 1;
  }

  return {
    clips,
    include_intro_outro: false,
    aspect_ratio: normalizeGenerationAspectRatio(aspectRatio),
    video_quality: normalizeMarketingVideoQuality(videoQuality),
    preserve_audio: false,
    aspect_mode: "crop",
  };
}

export function durationToSeconds(value: string): number {
  const parsed = Number.parseInt(value.replace(/[^0-9]/g, ""), 10);
  return Number.isFinite(parsed) ? parsed : 20;
}
