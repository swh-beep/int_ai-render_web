import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchVideoJobStatus, requestCompile, requestMarketingCompile, requestMarketingSourceGeneration } from "./videoMvp";

const jsonResponse = (body: unknown, init?: ResponseInit) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });

describe("video mvp api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requests marketing source generation through the local threaded endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ job_id: "local-source-job-1" }));
    const payload = {
      items: [{
        url: "https://cdn.example/start-1.png",
        motion: "custom",
        effect: "none",
        custom_motion_prompt: "slow camera push",
        custom_effect_prompt: null,
        duration: "5",
      }],
      cfg_scale: 0.5,
      aspect_ratio: "9:16" as const,
      video_quality: "1080p" as const,
      sound: "off" as const,
    };

    await expect(requestMarketingSourceGeneration(payload)).resolves.toBe("local-source-job-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/video-mvp/generate-sources-local",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    );
  });

  it("requests final compilation through the video-mvp compile endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ job_id: "compile-job-1" }));
    const payload = {
      clips: [{
        video_url: "https://cdn.example/clip-1.mp4",
        speed: 1,
        trim_start: 0,
        trim_end: 5,
        reverse: false,
        flip_horizontal: false,
      }],
      aspect_ratio: "9:16",
      video_quality: "1080p" as const,
      preserve_audio: true,
      aspect_mode: "crop",
    };

    await expect(requestCompile(payload)).resolves.toBe("compile-job-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/video-mvp/compile",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    );
  });

  it("requests marketing final compilation through the local threaded compile endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ job_id: "local-compile-job-1" }));
    const payload = {
      clips: [{
        video_url: "https://cdn.example/clip-1.mp4",
        speed: 1,
        trim_start: 0,
        trim_end: 5,
        reverse: false,
        flip_horizontal: false,
      }],
      aspect_ratio: "9:16",
      video_quality: "1080p" as const,
      preserve_audio: true,
      aspect_mode: "crop",
    };

    await expect(requestMarketingCompile(payload)).resolves.toBe("local-compile-job-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/video-mvp/compile-local",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    );
  });

  it("polls compile status without caching", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({
      status: "COMPLETED",
      progress: 100,
      result_url: "https://cdn.example/final.mp4",
    }));

    await expect(fetchVideoJobStatus("compile-job-1")).resolves.toEqual({
      status: "COMPLETED",
      progress: 100,
      result_url: "https://cdn.example/final.mp4",
    });

    expect(fetchMock).toHaveBeenCalledWith("/video-mvp/status/compile-job-1", { cache: "no-store" });
  });
});
