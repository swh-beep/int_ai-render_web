import { readApiError } from "./outputs";
import { buildImageEditFormData, buildRealPhotoFormData, type ImageEditFormOptions } from "../domain/imageStudio";
import type { QueuedJobResponse } from "./jobs";

async function postImageStudioForm(endpoint: string, formData: FormData): Promise<string> {
  const response = await fetch(endpoint, { method: "POST", body: formData });
  if (!response.ok) {
    throw new Error(await readApiError(response, `이미지 생성 요청 실패 (${response.status})`));
  }

  const payload = (await response.json()) as QueuedJobResponse;
  if (!payload.job_id) {
    throw new Error(payload.error ?? "Job queue failed");
  }
  return payload.job_id;
}

export function requestRealPhoto(sourceFiles: File[], instructions = ""): Promise<string> {
  return postImageStudioForm("/async/generate-frontal-view", buildRealPhotoFormData(sourceFiles, instructions));
}

export function requestImageEdit(options: ImageEditFormOptions): Promise<string> {
  return postImageStudioForm("/async/generate-image-edit", buildImageEditFormData(options));
}
