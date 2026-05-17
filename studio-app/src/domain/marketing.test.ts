import { describe, expect, it } from "vitest";

import {
  addCutPrompt,
  buildCompilePayloadFromApprovedItems,
  buildCompilePayload,
  buildSourceGenerationPayload,
  getCompileBlockers,
  normalizeSourceDurationSec,
  moveImage,
  removeCutPrompt,
  removeImage,
  type MarketingImageItem,
  type MarketingVideoAttempt,
  validateImageSelection,
} from "./marketing";

const imageFile = (name: string) => new File(["x"], name, { type: "image/png" });

describe("marketing domain", () => {
  it("validates that 1 to 10 images are selected", () => {
    expect(() => validateImageSelection([])).toThrow("이미지는 1~10장을 선택해야 합니다.");
    expect(validateImageSelection([imageFile("1.png")])).toHaveLength(1);
    expect(validateImageSelection(Array.from({ length: 3 }, (_, index) => imageFile(`${index}.png`)))).toHaveLength(3);
    expect(() => validateImageSelection(Array.from({ length: 11 }, (_, index) => imageFile(`${index}.png`)))).toThrow(
      "이미지는 1~10장을 선택해야 합니다.",
    );
  });

  it("rejects non-image files", () => {
    const files = [new File(["x"], "notes.txt", { type: "text/plain" })];

    expect(() => validateImageSelection(files)).toThrow("이미지 파일만 업로드할 수 있습니다.");
  });

  it("moves and removes images without mutating the original order", () => {
    const files = [imageFile("a.png"), imageFile("b.png"), imageFile("c.png")];

    expect(moveImage(files, 2, -1).map((file) => file.name)).toEqual(["a.png", "c.png", "b.png"]);
    expect(removeImage(files, 1).map((file) => file.name)).toEqual(["a.png", "c.png"]);
    expect(files.map((file) => file.name)).toEqual(["a.png", "b.png", "c.png"]);
  });

  it("adds and removes cut prompts while keeping a minimum of three cuts", () => {
    const prompts = ["open", "wide", "detail"];
    const added = addCutPrompt(prompts);

    expect(added).toEqual(["open", "wide", "detail", "추가 컷 - 제품과 공간 연결"]);
    expect(removeCutPrompt(added, 1)).toEqual(["open", "detail", "추가 컷 - 제품과 공간 연결"]);
    expect(removeCutPrompt(prompts, 1)).toEqual(prompts);
  });

  it("maps uploaded images to Kling source generation payloads", () => {
    const payload = buildSourceGenerationPayload({
      imageUrls: ["/outputs/a.png", "/outputs/b.png", "/outputs/c.png"],
      cutPrompts: ["opening", "wide", "detail"],
      targetDurationsSec: [3, 5, 10],
      globalPrompt: "warm camera motion",
      language: "한국어",
    });

    expect(payload.cfg_scale).toBe(0.5);
    expect(payload.aspect_ratio).toBe("9:16");
    expect(payload.items).toHaveLength(3);
    expect(payload.items[0]).toMatchObject({
      url: "/outputs/a.png",
      motion: "custom",
      effect: "none",
      custom_effect_prompt: null,
      duration: "3",
    });
    expect(payload.items.map((item) => item.duration)).toEqual(["3", "5", "10"]);
    expect(payload.items[0].custom_motion_prompt).toContain("cut 1: opening");
    expect(payload.items[0].custom_motion_prompt).toContain("global direction: warm camera motion");
    expect(payload.items[0].custom_motion_prompt).not.toContain("cinematic interior reel");
    expect(payload.items[0].custom_motion_prompt).not.toContain("tone:");
    expect(payload.items[0].custom_motion_prompt).not.toContain("platform:");
    expect(payload.items[0].custom_motion_prompt).not.toContain("audience:");
    expect(payload.items[0].custom_motion_prompt).not.toContain("goal:");
  });

  it("maps optional end frame URLs into Kling source generation payloads", () => {
    const payload = buildSourceGenerationPayload({
      imageUrls: ["https://cdn.example/start-a.png", "https://cdn.example/start-b.png"],
      endImageUrls: ["https://cdn.example/end-a.png", undefined],
      cutPrompts: ["install sofa", "camera move"],
      targetDurationsSec: [5, 7],
      globalPrompt: "show furniture appearing in the room",
      language: "한국어",
    });

    expect(payload.items[0]).toMatchObject({
      url: "https://cdn.example/start-a.png",
      end_url: "https://cdn.example/end-a.png",
      duration: "5",
    });
    expect(payload.items[1]).toMatchObject({
      url: "https://cdn.example/start-b.png",
      duration: "7",
    });
    expect(payload.items[1]).not.toHaveProperty("end_url");
  });

  it("maps selected aspect ratio into source and compile payloads", () => {
    const sourcePayload = buildSourceGenerationPayload({
      imageUrls: ["https://cdn.example/start-a.png"],
      cutPrompts: ["landscape camera move"],
      aspectRatio: "16:9",
      globalPrompt: "wide shot",
      language: "한국어",
    });

    expect(sourcePayload.aspect_ratio).toBe("16:9");
    expect(buildCompilePayload(["/outputs/a.mp4"], 5, "16:9").aspect_ratio).toBe("16:9");
  });

  it("normalizes invalid source durations to the 5 second default", () => {
    expect(normalizeSourceDurationSec(3)).toBe(3);
    expect(normalizeSourceDurationSec("10초")).toBe(10);
    expect(normalizeSourceDurationSec(15)).toBe(5);
    expect(normalizeSourceDurationSec(undefined)).toBe(5);
  });

  it("maps generated clips to compile payloads with 9:16 defaults", () => {
    const payload = buildCompilePayload(["/outputs/a.mp4", "/outputs/b.mp4"], 12);

    expect(payload.aspect_ratio).toBe("9:16");
    expect(payload.aspect_mode).toBe("crop");
    expect(payload.include_intro_outro).toBe(false);
    expect(payload.clips).toEqual([
      { video_url: "/outputs/a.mp4", speed: 1, trim_start: 0, trim_end: 5, reverse: false, flip_horizontal: false },
      { video_url: "/outputs/b.mp4", speed: 1, trim_start: 0, trim_end: 5, reverse: false, flip_horizontal: false },
      { video_url: "/outputs/a.mp4", speed: 1, trim_start: 0, trim_end: 2, reverse: false, flip_horizontal: false },
    ]);
  });

  it("builds compile payloads from approved non-deleted attempts only", () => {
    const completed = (attemptId: string, videoUrl: string, durationSec = 5): MarketingVideoAttempt => ({
      attemptId,
      sourceJobId: `job-${attemptId}`,
      index: 0,
      prompt: `prompt-${attemptId}`,
      videoUrl,
      status: "COMPLETED",
      durationSec: durationSec as MarketingVideoAttempt["durationSec"],
      createdAt: "2026-05-09T00:00:00.000Z",
    });
    const items: MarketingImageItem[] = [
      {
        clientImageId: "a",
        file: imageFile("a.png"),
        order: 2,
        prompt: "a",
        targetDurationSec: 5,
        attempts: [completed("attempt-a", "/outputs/a.mp4", 7)],
        approvedAttemptId: "attempt-a",
      },
      {
        clientImageId: "b",
        file: imageFile("b.png"),
        order: 1,
        prompt: "b",
        targetDurationSec: 5,
        attempts: [completed("attempt-b", "/outputs/b.mp4", 4)],
        approvedAttemptId: "attempt-b",
      },
      {
        clientImageId: "c",
        file: imageFile("c.png"),
        order: 3,
        prompt: "c",
        targetDurationSec: 5,
        attempts: [completed("attempt-c", "/outputs/c.mp4")],
        approvedAttemptId: "attempt-c",
        isDeleted: true,
      },
    ];

    expect(getCompileBlockers(items)).toHaveLength(0);
    const payload = buildCompilePayloadFromApprovedItems(items, "16:9");
    expect(payload.aspect_ratio).toBe("16:9");
    expect(payload.clips).toEqual([
      { video_url: "/outputs/b.mp4", speed: 1, trim_start: 0, trim_end: 4, reverse: false, flip_horizontal: false },
      { video_url: "/outputs/a.mp4", speed: 1, trim_start: 0, trim_end: 7, reverse: false, flip_horizontal: false },
    ]);
  });

  it("reports non-deleted images without completed approved attempts as compile blockers", () => {
    const items: MarketingImageItem[] = [
      {
        clientImageId: "a",
        file: imageFile("a.png"),
        order: 1,
        prompt: "a",
        targetDurationSec: 5,
        attempts: [{ attemptId: "failed", sourceJobId: "job", index: 0, prompt: "a", status: "FAILED", durationSec: 5, createdAt: "now" }],
        approvedAttemptId: "failed",
      },
    ];

    expect(getCompileBlockers(items).map((item) => item.clientImageId)).toEqual(["a"]);
  });

  it("does not expose legacy marketing database endpoint constants", async () => {
    const modules = await Promise.all([
      import("./marketing?raw"),
      import("../api/outputs?raw"),
      import("../api/videoMvp?raw"),
      import("../pages/MarketingPage?raw"),
    ]);
    const source = modules.map((module) => module.default).join("\n");

    expect(source).not.toContain("/marketing/save");
    expect(source).not.toContain("/marketing/history");
  });
});
