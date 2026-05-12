type PresignedUploadItem = {
  client_id?: string | null;
  upload_url?: string;
  public_url?: string;
  read_url?: string;
  content_type?: string;
};

export type UploadedOutputAsset = {
  publicUrl: string;
  readUrl?: string;
};

type OutputUploadOptions = {
  folderSuffix?: string;
  purpose?: "marketing-kling";
  groupId?: string;
  assetType?: "images" | "images/start" | "images/end" | "videos" | "final";
  imageRole?: "start" | "end";
};

export async function uploadOutputImage(file: File): Promise<string> {
  const [url] = await uploadOutputImages([file]);
  if (!url) throw new Error("이미지 업로드 결과 URL이 없습니다.");
  return url;
}

export async function uploadOutputImages(files: File[], options: OutputUploadOptions = {}): Promise<string[]> {
  const assets = await uploadOutputImageAssets(files, options);
  return assets.map((asset) => asset.publicUrl);
}

export async function uploadOutputImageAssets(files: File[], options: OutputUploadOptions = {}): Promise<UploadedOutputAsset[]> {
  if (files.length === 0) return [];

  const presignedAssets = await uploadOutputImagesViaPresignedUrls(files, options);
  if (presignedAssets) return presignedAssets;
  if (options.purpose) throw new Error("목적별 S3 업로드 URL 발급이 필요합니다.");

  const assets: UploadedOutputAsset[] = [];
  for (const file of files) {
    const url = await uploadOutputImageViaFormData(file);
    assets.push({ publicUrl: url, readUrl: url });
  }
  return assets;
}

async function uploadOutputImageViaFormData(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/outputs/upload", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) throw new Error(await readApiError(response, `이미지 업로드 실패 (${response.status})`));

  const payload = (await response.json()) as { url?: string };
  if (!payload.url) throw new Error("이미지 업로드 결과 URL이 없습니다.");
  return payload.url;
}

async function uploadOutputImagesViaPresignedUrls(files: File[], options: OutputUploadOptions): Promise<UploadedOutputAsset[] | null> {
  const response = await fetch("/api/outputs/presign-upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      folder_suffix: options.folderSuffix,
      purpose: options.purpose,
      group_id: options.groupId,
      asset_type: options.assetType === "images" && options.imageRole ? `images/${options.imageRole}` : options.assetType,
      files: files.map((file, index) => ({
        client_id: String(index),
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size: file.size,
      })),
    }),
  });
  if (response.status === 404 || response.status === 501) return null;
  if (!response.ok) throw new Error(await readApiError(response, `이미지 업로드 URL 발급 실패 (${response.status})`));

  const payload = (await response.json()) as { items?: PresignedUploadItem[] };
  if (!payload.items || payload.items.length !== files.length) throw new Error("이미지 업로드 URL 응답이 올바르지 않습니다.");

  await Promise.all(
    payload.items.map(async (item, index) => {
      if (!item.upload_url || !item.public_url) throw new Error("이미지 업로드 URL 응답이 올바르지 않습니다.");
      const file = files[index];
      const uploadResponse = await fetch(item.upload_url, {
        method: "PUT",
        headers: { "Content-Type": item.content_type || file.type || "application/octet-stream" },
        body: file,
      });
      if (!uploadResponse.ok) throw new Error(`S3 이미지 업로드 실패 (${uploadResponse.status})`);
    }),
  );

  return payload.items.map((item) => ({
    publicUrl: item.public_url as string,
    readUrl: item.read_url ?? item.public_url,
  }));
}

export async function publishOutputAsset(url: string, options: OutputUploadOptions): Promise<string> {
  const response = await fetch("/api/outputs/publish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      folder_suffix: options.folderSuffix,
      purpose: options.purpose,
      group_id: options.groupId,
      asset_type: options.assetType,
    }),
  });
  if (!response.ok) throw new Error(await readApiError(response, `작업물 S3 저장 실패 (${response.status})`));

  const payload = (await response.json()) as { public_url?: string; url?: string };
  const publicUrl = payload.public_url ?? payload.url;
  if (!publicUrl) throw new Error("작업물 S3 저장 결과 URL이 없습니다.");
  return publicUrl;
}

export async function uploadOutputVideo(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/outputs/upload-video", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) throw new Error(await readApiError(response, `영상 업로드 실패 (${response.status})`));

  const payload = (await response.json()) as { url?: string };
  if (!payload.url) throw new Error("영상 업로드 결과 URL이 없습니다.");
  return payload.url;
}

export async function readApiError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: string; detail?: string };
    return payload.error ?? payload.detail ?? fallback;
  } catch {
    return response.text();
  }
}
