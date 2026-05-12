import { describe, expect, it } from "vitest";

import { buildImageEditFormData, buildRealPhotoFormData, imageStudioEndpointForMode, validateImageFiles } from "./imageStudio";

const image = (name: string) => new File(["x"], name, { type: "image/png" });

describe("image studio domain", () => {
  it("validates supported image files", () => {
    expect(() => validateImageFiles([])).toThrow("이미지 파일을 선택하세요.");
    expect(() => validateImageFiles([new File(["x"], "doc.txt", { type: "text/plain" })])).toThrow(
      "png, jpg, jpeg, webp 이미지만 업로드할 수 있습니다.",
    );
    expect(validateImageFiles([image("room.png")])).toHaveLength(1);
  });

  it("builds real photo FormData with input_photos and optional instructions", () => {
    const formData = buildRealPhotoFormData([image("a.png"), image("b.png")], "front view");

    expect(formData.getAll("input_photos")).toHaveLength(2);
    expect(formData.get("instructions")).toBe("front view");
    expect(imageStudioEndpointForMode("real-photo")).toBe("/async/generate-frontal-view");
  });

  it("builds edit and decorate FormData with mode, instructions, references, and optional mask", () => {
    const mask = new Blob(["mask"], { type: "image/png" });
    const editData = buildImageEditFormData({
      sourceFiles: [image("source.png")],
      mode: "edit",
      instructions: "",
      referenceFiles: [image("ref.png")],
      maskBlob: mask,
    });

    expect(editData.getAll("input_photos")).toHaveLength(2);
    expect(editData.get("mode")).toBe("edit");
    expect(editData.get("instructions")).toBe("Rearrange furniture for better flow.");
    expect(editData.get("mask")).toBeInstanceOf(File);
    expect(imageStudioEndpointForMode("edit-image")).toBe("/async/generate-image-edit");

    const decorData = buildImageEditFormData({
      sourceFiles: [image("decor.png")],
      mode: "decorate",
      instructions: "add warm lights",
    });
    expect(decorData.get("mode")).toBe("decorate");
    expect(decorData.get("instructions")).toBe("add warm lights");
    expect(decorData.get("mask")).toBeNull();
  });

  it("does not expose database write endpoints", async () => {
    const modules = await Promise.all([import("./imageStudio?raw"), import("../api/imageStudio?raw")]);
    const source = modules.map((module) => module.default).join("\n");

    expect(source).not.toContain("/api/marketing");
    expect(source).not.toContain("/marketing/save");
    expect(source).not.toContain("/marketing/history");
  });
});
