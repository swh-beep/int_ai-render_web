export type VideoSourceMotion =
  | "static"
  | "orbit_r_slow"
  | "orbit_l_slow"
  | "orbit_r_fast"
  | "orbit_l_fast"
  | "zoom_in_slow"
  | "zoom_out_slow"
  | "zoom_in_fast"
  | "zoom_out_fast"
  | "custom";

export type VideoSourceEffect = "none" | "sunlight" | "lights_on" | "blinds" | "plants" | "door_open" | "custom";

export type AspectRatio = "16:9" | "1:1" | "4:5" | "9:16";
export type AspectMode = "crop" | "fill";

export type SourceClipRequestOptions = {
  imageUrl: string;
  motion: VideoSourceMotion;
  effect: VideoSourceEffect;
  customMotionPrompt?: string;
  customEffectPrompt?: string;
};

export type SourceClipPayload = {
  items: Array<{
    url: string;
    motion: VideoSourceMotion;
    effect: VideoSourceEffect;
    custom_motion_prompt: string | null;
    custom_effect_prompt: string | null;
  }>;
  cfg_scale: number;
};

export type AssembleClip = {
  id: string;
  file: File;
  previewUrl: string;
  name: string;
  duration: number;
  speed: number;
  trimStart: number;
  trimEnd: number;
  reverse: boolean;
  flipHorizontal: boolean;
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
  aspect_ratio: AspectRatio;
  aspect_mode: AspectMode;
};

const allowedImageTypes = new Set(["image/png", "image/jpeg", "image/jpg", "image/webp"]);
const allowedVideoTypes = new Set(["video/mp4", "video/quicktime", "video/webm"]);

export function validateSourceImage(files: File[]): File {
  if (files.length === 0) {
    throw new Error("Upload one source image.");
  }
  const [file] = files;
  if (!allowedImageTypes.has(file.type)) {
    throw new Error("png, jpg, jpeg, webp 이미지만 source image로 사용할 수 있습니다.");
  }
  return file;
}

export function validateVideoFiles(files: File[]): File[] {
  if (files.length === 0) {
    throw new Error("Upload at least one clip to assemble.");
  }
  const invalid = files.find((file) => !allowedVideoTypes.has(file.type));
  if (invalid) {
    throw new Error("mp4, mov, webm 영상만 업로드할 수 있습니다.");
  }
  return files;
}

export function buildSourceClipPayload(options: SourceClipRequestOptions): SourceClipPayload {
  return {
    items: [
      {
        url: options.imageUrl,
        motion: options.motion,
        effect: options.effect,
        custom_motion_prompt: options.motion === "custom" ? options.customMotionPrompt?.trim() || null : null,
        custom_effect_prompt: options.effect === "custom" ? options.customEffectPrompt?.trim() || null : null,
      },
    ],
    cfg_scale: 0.5,
  };
}

export function createAssembleClip(file: File, previewUrl: string, duration = 5): AssembleClip {
  return {
    id: `${file.name}-${file.size}-${file.lastModified}`,
    file,
    previewUrl,
    name: file.name,
    duration,
    speed: 1,
    trimStart: 0,
    trimEnd: Math.max(0.1, Math.min(5, duration || 5)),
    reverse: false,
    flipHorizontal: false,
  };
}

export function moveClip<T>(clips: T[], index: number, offset: -1 | 1): T[] {
  const targetIndex = index + offset;
  if (targetIndex < 0 || targetIndex >= clips.length) return clips.slice();
  const next = clips.slice();
  const [clip] = next.splice(index, 1);
  next.splice(targetIndex, 0, clip);
  return next;
}

export function removeClip<T>(clips: T[], index: number): T[] {
  return clips.filter((_, clipIndex) => clipIndex !== index);
}

export function updateClip(clips: AssembleClip[], id: string, patch: Partial<AssembleClip>): AssembleClip[] {
  return clips.map((clip) => (clip.id === id ? { ...clip, ...patch } : clip));
}

export function buildCompilePayload(
  clips: AssembleClip[],
  materializedUrls: string[],
  aspectRatio: AspectRatio,
  aspectMode: AspectMode,
): CompilePayload {
  return {
    clips: clips.map((clip, index) => ({
      video_url: materializedUrls[index],
      speed: clip.speed,
      trim_start: clip.trimStart,
      trim_end: clip.trimEnd,
      reverse: clip.reverse,
      flip_horizontal: clip.flipHorizontal,
    })),
    include_intro_outro: false,
    aspect_ratio: aspectRatio,
    aspect_mode: aspectMode,
  };
}
