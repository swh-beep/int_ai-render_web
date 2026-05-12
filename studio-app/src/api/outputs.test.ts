import { afterEach, describe, expect, it, vi } from "vitest";

import { publishOutputAsset, uploadOutputImage, uploadOutputImages } from "./outputs";

const jsonResponse = (body: unknown, init?: ResponseInit) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });

describe("outputs api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("requests batch presigned urls and uploads files directly", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            { upload_url: "https://upload.example/1", public_url: "https://cdn.example/1.png", content_type: "image/png" },
            { upload_url: "https://upload.example/2", public_url: "https://cdn.example/2.jpg", content_type: "image/jpeg" },
          ],
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    const files = [
      new File(["a"], "room-a.png", { type: "image/png" }),
      new File(["b"], "room-b.jpg", { type: "image/jpeg" }),
    ];

    await expect(uploadOutputImages(files, { folderSuffix: "marketing-video" })).resolves.toEqual(["https://cdn.example/1.png", "https://cdn.example/2.jpg"]);

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/outputs/presign-upload",
      expect.objectContaining({ method: "POST", body: expect.stringContaining('"folder_suffix":"marketing-video"') }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "https://upload.example/1",
      expect.objectContaining({ method: "PUT", body: files[0] }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "https://upload.example/2",
      expect.objectContaining({ method: "PUT", body: files[1] }),
    );
  });

  it("keeps single image uploads compatible with the batch endpoint", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ items: [{ upload_url: "https://upload.example/1", public_url: "https://cdn.example/1.png" }] }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    await expect(uploadOutputImage(new File(["a"], "room.png", { type: "image/png" }))).resolves.toBe("https://cdn.example/1.png");
  });

  it("does not fall back to generic uploads for marketing-kling assets", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(jsonResponse({ error: "no s3" }, { status: 501 }));

    await expect(
      uploadOutputImages([new File(["a"], "room.png", { type: "image/png" })], {
        purpose: "marketing-kling",
        groupId: "group-1",
        assetType: "images",
      }),
    ).rejects.toThrow("목적별 S3 업로드 URL 발급이 필요합니다.");

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("requests marketing-kling start and end image subfolders", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ upload_url: "https://upload.example/start", public_url: "https://cdn.example/start.png" }],
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ upload_url: "https://upload.example/end", public_url: "https://cdn.example/end.png" }],
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    await uploadOutputImages([new File(["a"], "start.png", { type: "image/png" })], {
      purpose: "marketing-kling",
      groupId: "group-1",
      assetType: "images",
      imageRole: "start",
    });
    await uploadOutputImages([new File(["b"], "end.png", { type: "image/png" })], {
      purpose: "marketing-kling",
      groupId: "group-1",
      assetType: "images",
      imageRole: "end",
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/outputs/presign-upload",
      expect.objectContaining({ body: expect.stringContaining('"asset_type":"images/start"') }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/outputs/presign-upload",
      expect.objectContaining({ body: expect.stringContaining('"asset_type":"images/end"') }),
    );
  });

  it("publishes generated local assets into a server-selected namespace", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ public_url: "https://cdn.example/final.mp4" }));

    await expect(
      publishOutputAsset("/outputs/final.mp4", {
        purpose: "marketing-kling",
        groupId: "group-1",
        assetType: "final",
      }),
    ).resolves.toBe("https://cdn.example/final.mp4");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/outputs/publish",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining('"asset_type":"final"'),
      }),
    );
  });
});
