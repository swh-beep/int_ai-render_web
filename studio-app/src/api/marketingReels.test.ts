import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveMarketingClipAttempt,
  createMarketingClipAttempt,
  createMarketingReelGroup,
  deleteMarketingReelClip,
  getMarketingReelGroup,
  listMarketingReelGroups,
  markMarketingReelGroupFailed,
  patchMarketingFinalResult,
  updateMarketingClipSourceImages,
  updateMarketingClipAttempt,
} from "./marketingReels";

const jsonResponse = (body: unknown, init?: ResponseInit) => new Response(JSON.stringify(body), {
  status: 200,
  headers: { "Content-Type": "application/json" },
  ...init,
});

describe("marketing reels api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates a reel group with image clip inputs", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ group_id: "group-1", clips: [] }));

    await expect(
      createMarketingReelGroup({
        globalPrompt: "warm",
        platform: "Instagram",
        tone: "Editorial",
        goal: "awareness",
        clips: [{ clientImageId: "client-1", sourceImageUrl: "/outputs/a.png", order: 1, prompt: "open", durationSec: 5 }],
      }),
    ).resolves.toEqual({ group_id: "group-1", clips: [] });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/marketing/reel-groups",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"duration_sec":5') }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/marketing/reel-groups",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"client_image_id":"client-1"') }),
    );
  });

  it("creates a reel group with start/end frame clip inputs", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ group_id: "group-1", clips: [] }));

    await createMarketingReelGroup({
      globalPrompt: "warm",
      platform: "Instagram",
      tone: "Editorial",
      goal: "awareness",
      clips: [
        {
          clientImageId: "client-1",
          sourceImageUrl: "",
          endImageUrl: undefined,
          generationMode: "START_END",
          order: 1,
          prompt: "install",
          durationSec: 5,
        },
      ],
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/marketing/reel-groups",
      expect.objectContaining({ body: expect.stringContaining('"generation_mode":"START_END"') }),
    );
  });

  it("creates and updates clip attempts", async () => {
    const attempt = {
      attempt_id: "attempt-1",
      clip_id: "clip-1",
      source_job_id: "job-1",
      source_job_item_index: 0,
      prompt: "open",
      duration_sec: 5 as const,
      status: "RUNNING" as const,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(attempt))
      .mockResolvedValueOnce(jsonResponse({ ...attempt, status: "COMPLETED", source_video_url: "/outputs/a.mp4" }));

    await createMarketingClipAttempt("group-1", attempt);
    await updateMarketingClipAttempt("group-1", "attempt-1", { status: "COMPLETED", source_video_url: "/outputs/a.mp4" });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/marketing/reel-groups/group-1/clip-attempts",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/marketing/reel-groups/group-1/clip-attempts/attempt-1",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("marks a reel group failed when generation cannot continue", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ group_id: "group-1" }));

    await expect(markMarketingReelGroupFailed("group-1")).resolves.toEqual({ group_id: "group-1" });

    expect(fetchMock).toHaveBeenCalledWith("/api/marketing/reel-groups/group-1/failed", { method: "PATCH" });
  });

  it("soft-deletes a clip from the marketing sequence", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({
      group_id: "group-1",
      clip_id: "clip-1",
      deleted_at: "2026-05-10T00:00:00+09:00",
    }));

    await expect(deleteMarketingReelClip("group-1", "clip-1")).resolves.toEqual({
      group_id: "group-1",
      clip_id: "clip-1",
      deleted_at: "2026-05-10T00:00:00+09:00",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/marketing/reel-groups/group-1/clips/clip-1/deleted",
      { method: "PATCH" },
    );
  });

  it("patches source image URLs after group-scoped S3 upload", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ group_id: "group-1", clips: [] }));

    await expect(
      updateMarketingClipSourceImages("group-1", {
        clips: [{ clip_id: "clip-1", source_image_url: "https://cdn.example/marketing-kling/group-1/images/a.png" }],
      }),
    ).resolves.toEqual({ group_id: "group-1", clips: [] });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/marketing/reel-groups/group-1/clips/source-images",
      expect.objectContaining({ method: "PATCH", body: expect.stringContaining('"source_image_url"') }),
    );
  });

  it("patches start/end frame URLs after group-scoped S3 upload", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ group_id: "group-1", clips: [] }));

    await updateMarketingClipSourceImages("group-1", {
      clips: [
        {
          clip_id: "clip-1",
          source_image_url: "https://cdn.example/marketing-kling/group-1/images/start/a.png",
          end_image_url: "https://cdn.example/marketing-kling/group-1/images/end/b.png",
          generation_mode: "START_END",
        },
      ],
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/marketing/reel-groups/group-1/clips/source-images",
      expect.objectContaining({ body: expect.stringContaining('"end_image_url"') }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/marketing/reel-groups/group-1/clips/source-images",
      expect.objectContaining({ body: expect.stringContaining('"generation_mode":"START_END"') }),
    );
  });

  it("approves attempts and patches final results", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ group_id: "group-1", clip_id: "clip-1", approved_attempt_id: "attempt-1" }))
      .mockResolvedValueOnce(jsonResponse({ group_id: "group-1" }));

    await approveMarketingClipAttempt("group-1", "clip-1", { attempt_id: "attempt-1" });
    await patchMarketingFinalResult("group-1", {
      compile_job_id: "compile-1",
      final_video_url: "/outputs/final.mp4",
      compile_payload_summary: { clips: 3 },
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/marketing/reel-groups/group-1/clips/clip-1/approval",
      expect.objectContaining({ method: "PATCH" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/marketing/reel-groups/group-1/final",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("reads shared history list and detail without cache", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ group_id: "group-1" }));

    await listMarketingReelGroups(10);
    await getMarketingReelGroup("group-1");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/marketing/reel-groups?limit=10", { cache: "no-store" });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/marketing/reel-groups/group-1", { cache: "no-store" });
  });

  it("surfaces api errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ detail: "no db" }, { status: 500 }));

    await expect(listMarketingReelGroups()).rejects.toThrow("no db");
  });
});
