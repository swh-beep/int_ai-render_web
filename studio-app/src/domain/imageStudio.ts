export type ImageStudioMode = "real-photo" | "edit-image" | "decorate-image";

export type ImageEditMode = "edit" | "decorate";

export type ImageEditFormOptions = {
  sourceFiles: File[];
  mode: ImageEditMode;
  instructions: string;
  referenceFiles?: File[];
  maskBlob?: Blob | null;
};

const allowedImageTypes = new Set(["image/png", "image/jpeg", "image/jpg", "image/webp"]);

export function validateImageFiles(files: File[], label = "이미지"): File[] {
  if (files.length === 0) {
    throw new Error(`${label} 파일을 선택하세요.`);
  }
  const invalid = files.find((file) => !allowedImageTypes.has(file.type));
  if (invalid) {
    throw new Error("png, jpg, jpeg, webp 이미지만 업로드할 수 있습니다.");
  }
  return files;
}

export function buildRealPhotoFormData(sourceFiles: File[], instructions = ""): FormData {
  validateImageFiles(sourceFiles, "Source photo");
  const formData = new FormData();
  sourceFiles.forEach((file) => formData.append("input_photos", file));
  if (instructions.trim()) {
    formData.append("instructions", instructions.trim());
  }
  return formData;
}

export function buildImageEditFormData({
  sourceFiles,
  mode,
  instructions,
  referenceFiles = [],
  maskBlob,
}: ImageEditFormOptions): FormData {
  validateImageFiles(sourceFiles, "Source image");
  if (referenceFiles.length > 0) {
    validateImageFiles(referenceFiles, "Reference image");
  }

  const formData = new FormData();
  sourceFiles.forEach((file) => formData.append("input_photos", file));
  referenceFiles.forEach((file) => formData.append("input_photos", file));
  formData.append("mode", mode);
  formData.append(
    "instructions",
    instructions.trim() || (mode === "edit" ? "Rearrange furniture for better flow." : "Make it cozy and stylish."),
  );
  if (mode === "edit" && maskBlob) {
    formData.append("mask", maskBlob, "edit_mask.png");
  }
  return formData;
}

export function imageStudioEndpointForMode(mode: ImageStudioMode): "/async/generate-frontal-view" | "/async/generate-image-edit" {
  return mode === "real-photo" ? "/async/generate-frontal-view" : "/async/generate-image-edit";
}
