import { describe, expect, it } from "vitest";

import {
  buildCompilePayload,
  buildSourceClipPayload,
  createAssembleClip,
  moveClip,
  removeClip,
  updateClip,
  validateSourceImage,
  validateVideoFiles,
} from "./videoStudio";

const image = (name = "room.png") => new File(["x"], name, { type: "image/png" });
const video = (name = "clip.mp4") => new File(["x"], name, { type: "video/mp4" });

describe("video studio domain", () => {
  it("validates source images and assemble videos", () => {
    expect(() => validateSourceImage([])).toThrow("Upload one source image.");
    expect(() => validateSourceImage([new File(["x"], "clip.mp4", { type: "video/mp4" })])).toThrow(
      "png, jpg, jpeg, webp 이미지만 source image로 사용할 수 있습니다.",
    );
    expect(validateSourceImage([image()]).name).toBe("room.png");

    expect(() => validateVideoFiles([])).toThrow("Upload at least one clip to assemble.");
    expect(() => validateVideoFiles([new File(["x"], "room.png", { type: "image/png" })])).toThrow(
      "mp4, mov, webm 영상만 업로드할 수 있습니다.",
    );
    expect(validateVideoFiles([video()])).toHaveLength(1);
  });

  it("maps source image controls to generate-sources payload", () => {
    const payload = buildSourceClipPayload({
      imageUrl: "/outputs/source.png",
      motion: "custom",
      effect: "custom",
      customMotionPrompt: "slow orbit",
      customEffectPrompt: "sunlight",
    });

    expect(payload).toEqual({
      items: [
        {
          url: "/outputs/source.png",
          motion: "custom",
          effect: "custom",
          custom_motion_prompt: "slow orbit",
          custom_effect_prompt: "sunlight",
        },
      ],
      cfg_scale: 0.5,
    });
  });

  it("moves, removes, and updates assemble clips immutably", () => {
    const clips = [createAssembleClip(video("a.mp4"), "blob:a"), createAssembleClip(video("b.mp4"), "blob:b")];

    expect(moveClip(clips, 1, -1).map((clip) => clip.name)).toEqual(["b.mp4", "a.mp4"]);
    expect(removeClip(clips, 0).map((clip) => clip.name)).toEqual(["b.mp4"]);
    expect(updateClip(clips, clips[0].id, { reverse: true, speed: 1.5 })[0]).toMatchObject({
      reverse: true,
      speed: 1.5,
    });
    expect(clips[0].reverse).toBe(false);
  });

  it("maps assemble state to compile payload", () => {
    const clips = [
      { ...createAssembleClip(video("a.mp4"), "blob:a"), speed: 1.25, trimStart: 0.5, trimEnd: 4, reverse: true },
      { ...createAssembleClip(video("b.mp4"), "blob:b"), flipHorizontal: true },
    ];

    const payload = buildCompilePayload(clips, ["/outputs/a.mp4", "/outputs/b.mp4"], "4:5", "fill");

    expect(payload).toEqual({
      clips: [
        { video_url: "/outputs/a.mp4", speed: 1.25, trim_start: 0.5, trim_end: 4, reverse: true, flip_horizontal: false },
        { video_url: "/outputs/b.mp4", speed: 1, trim_start: 0, trim_end: 5, reverse: false, flip_horizontal: true },
      ],
      include_intro_outro: false,
      aspect_ratio: "4:5",
      aspect_mode: "fill",
    });
  });

  it("does not expose database write endpoints", async () => {
    const modules = await Promise.all([import("./videoStudio?raw"), import("../api/videoMvp?raw")]);
    const source = modules.map((module) => module.default).join("\n");

    expect(source).not.toContain("/api/marketing");
    expect(source).not.toContain("/marketing/save");
    expect(source).not.toContain("/marketing/history");
  });
});
