export type ContentType = "popup" | "cinematic" | "install";
export type MarketingGenerationMode = "START_ONLY" | "START_END" | "NEXT_START_AS_END";
export type MarketingAspectRatio = "9:16" | "16:9";

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
  aspect_ratio: MarketingAspectRatio;
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
  aspect_ratio: MarketingAspectRatio;
  aspect_mode: "crop";
};

export type MarketingBrief = {
  imageUrls: string[];
  endImageUrls?: Array<string | undefined>;
  cutPrompts: string[];
  targetDurationsSec?: SourceDurationSec[];
  contentType: ContentType;
  globalPrompt: string;
  tone: string;
  platform: string;
  audience: string;
  goal: string;
  language: string;
  aspectRatio?: MarketingAspectRatio;
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

export const minimumImageCount = 3;
export const maximumImageCount = 10;
export const defaultSourceDurationSec: SourceDurationSec = 5;
export const allowedSourceDurationsSec: SourceDurationSec[] = [3, 4, 5, 6, 7, 8, 9, 10];
export const defaultMarketingAspectRatio: MarketingAspectRatio = "9:16";
export const allowedMarketingAspectRatios: MarketingAspectRatio[] = ["9:16", "16:9"];

export const defaultCutPrompts = ["오프닝 - 정면 클로즈업", "와이드 컷 - 공간감 강조", "디테일 텍스처 컷"];

export const typeCopy: Record<ContentType, { hook: string; caption: string; cta: string; direction: string }> = {
  popup: {
    hook: "신제품이 공간의 첫인상을 바꾸는 순간",
    caption: "팝업 쇼룸에서 만나는 새로운 컬렉션.",
    cta: "런칭 일정과 쇼룸 정보를 확인하세요.",
    direction: "furniture popup launch reel, energetic showroom reveal, premium product presence",
  },
  cinematic: {
    hook: "3초 안에 시선을 머물게 하는 디자인",
    caption: "빛이 머무는 자리에, 봄의 라운지.",
    cta: "지금 쇼룸에서 직접 만나보세요.",
    direction: "cinematic interior reel, warm editorial lighting, slow refined camera movement",
  },
  install: {
    hook: "빈 공간이 완성되는 가장 자연스러운 흐름",
    caption: "배치, 균형, 마감까지 한 장면으로 보여드립니다.",
    cta: "공간 솔루션 상담을 시작하세요.",
    direction: "furniture installation reel, before and after flow, practical spatial transformation",
  },
};

export function validateImageSelection(files: File[]): File[] {
  if (files.length < minimumImageCount || files.length > maximumImageCount) {
    throw new Error("이미지는 3~10장을 선택해야 합니다.");
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
  return allowedMarketingAspectRatios.includes(value as MarketingAspectRatio)
    ? value as MarketingAspectRatio
    : defaultMarketingAspectRatio;
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
  const copy = typeCopy[brief.contentType] ?? typeCopy.popup;
  return [
    copy.direction,
    `cut ${index + 1}: ${cutPrompt}`,
    `global direction: ${brief.globalPrompt}`,
    `tone: ${brief.tone}`,
    `platform: ${brief.platform}`,
    `audience: ${brief.audience}`,
    `goal: ${brief.goal}`,
    `language: ${brief.language}`,
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
    aspect_ratio: normalizeMarketingAspectRatio(brief.aspectRatio),
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
  aspectRatio: MarketingAspectRatio = defaultMarketingAspectRatio,
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
    aspect_ratio: normalizeMarketingAspectRatio(aspectRatio),
    aspect_mode: "crop",
  };
}

export function buildCompilePayload(
  sourceUrls: string[],
  targetDurationSec: number,
  aspectRatio: MarketingAspectRatio = defaultMarketingAspectRatio,
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
    aspect_ratio: normalizeMarketingAspectRatio(aspectRatio),
    aspect_mode: "crop",
  };
}

export function durationToSeconds(value: string): number {
  const parsed = Number.parseInt(value.replace(/[^0-9]/g, ""), 10);
  return Number.isFinite(parsed) ? parsed : 20;
}
