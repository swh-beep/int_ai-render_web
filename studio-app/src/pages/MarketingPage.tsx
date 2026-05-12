import { useEffect, useMemo, useRef, useState } from "react";

import {
  approveMarketingClipAttempt,
  createMarketingClipAttempt,
  createMarketingReelGroup,
  deleteMarketingReelClip,
  getMarketingReelGroup,
  listMarketingReelGroups,
  markMarketingReelGroupFailed,
  patchMarketingFinalResult,
  updateMarketingReelGroupTitle,
  updateMarketingClipSourceImages,
  updateMarketingClipAttempt,
  type MarketingFinalResultPayload,
  type MarketingReelClipDetail,
  type MarketingReelGroupDetail,
  type MarketingReelGroupListItem,
} from "../api/marketingReels";
import { publishOutputAsset, uploadOutputImageAssets } from "../api/outputs";
import {
  downloadUrlForResult,
  fetchVideoJobStatus,
  requestCompile,
  requestSourceGeneration,
  type VideoJobState,
} from "../api/videoMvp";
import {
  allowedSourceDurationsSec,
  allowedMarketingAspectRatios,
  buildCompilePayloadFromApprovedItems,
  buildKlingPrompt,
  buildSourceGenerationPayload,
  defaultSourceDurationSec,
  defaultMarketingAspectRatio,
  generationModeLabel,
  getCompileBlockers,
  moveImage,
  normalizeMarketingAspectRatio,
  normalizeSourceDurationSec,
  removeImage,
  requiresEndFrame,
  type ContentType,
  type MarketingAspectRatio,
  type MarketingGenerationMode,
  type MarketingImageItem,
  type MarketingVideoAttempt,
  type SourceDurationSec,
  typeCopy,
  validateImageSelection,
} from "../domain/marketing";

type Step = 1 | 2 | 3;

type ClipDraft = MarketingImageItem & {
  previewUrl: string;
  clipId?: string;
  viewingAttemptId?: string;
  sourceGenerationUrl?: string;
  endGenerationUrl?: string;
};

const contentTypeOptions: Array<{ value: ContentType; label: string }> = [
  { value: "popup", label: "가구 Popup" },
  { value: "cinematic", label: "Cinematic 영상" },
  { value: "install", label: "가구 설치 영상" },
];

const aspectRatioLabels: Record<MarketingAspectRatio, string> = {
  "9:16": "9:16 세로",
  "16:9": "16:9 가로",
};

function formatDateTitlePart(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function makeId(prefix: string): string {
  const randomId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `${prefix}-${randomId}`;
}

function makeAttempt(params: {
  clip: ClipDraft;
  sourceJobId: string;
  sourceJobItemIndex: number;
  prompt: string;
  status: MarketingVideoAttempt["status"];
}): MarketingVideoAttempt {
  return {
    attemptId: makeId("attempt"),
    sourceJobId: params.sourceJobId,
    index: params.sourceJobItemIndex,
    prompt: params.prompt,
    status: params.status,
    durationSec: params.clip.targetDurationSec,
    createdAt: new Date().toISOString(),
  };
}

function toApiGenerationMode(mode: MarketingGenerationMode | undefined): "START_ONLY" | "START_END" {
  return requiresEndFrame(mode) ? "START_END" : "START_ONLY";
}

export function MarketingPage() {
  const objectUrlsRef = useRef(new Set<string>());
  const [activeStep, setActiveStep] = useState<Step>(1);
  const [contentType, setContentType] = useState<ContentType>("popup");
  const [aspectRatio, setAspectRatio] = useState<MarketingAspectRatio>(defaultMarketingAspectRatio);
  const [clips, setClips] = useState<ClipDraft[]>([]);
  const [globalPrompt, setGlobalPrompt] = useState(
    "따뜻한 자연광 속에서 절제된 가구의 디테일을 보여주는 시네마틱 릴스. 부드러운 카메라 무빙, 베이지와 오크 톤.",
  );
  const [tone, setTone] = useState("Editorial");
  const [platform, setPlatform] = useState("Instagram");
  const [goal, setGoal] = useState("신상 라운지 컬렉션 인지도 확대");
  const [groupId, setGroupId] = useState("");
  const [status, setStatus] = useState("이미지 3~10장을 선택하고 1차 비디오 생성을 시작하세요.");
  const [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const [isBusy, setIsBusy] = useState(false);
  const [regeneratingClipId, setRegeneratingClipId] = useState("");
  const [regeneratingClipIds, setRegeneratingClipIds] = useState<string[]>([]);
  const [regeneratePrompt, setRegeneratePrompt] = useState("");
  const [selectedReference, setSelectedReference] = useState<MarketingReelGroupDetail | null>(null);
  const [selectedReferenceClipId, setSelectedReferenceClipId] = useState("");
  const [historyItems, setHistoryItems] = useState<MarketingReelGroupListItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [finalUrl, setFinalUrl] = useState("");
  const [finalTitle, setFinalTitle] = useState("");
  const [finalTitleEdited, setFinalTitleEdited] = useState(false);
  const [historyTitleDraft, setHistoryTitleDraft] = useState("");
  const [historyTitleSaving, setHistoryTitleSaving] = useState(false);
  const [finalSaveError, setFinalSaveError] = useState("");
  const [lastFinalPersistPayload, setLastFinalPersistPayload] = useState<MarketingFinalResultPayload | null>(null);

  const activeClips = clips.filter((clip) => !clip.isDeleted);
  const blockers = getCompileBlockers(activeClips);
  const canCompile = activeClips.length > 0 && blockers.length === 0;
  const hasStartedGeneration = Boolean(groupId) || clips.some((clip) => clip.attempts.length > 0);
  const isSequenceLocked = Boolean(groupId);
  const copy = typeCopy[contentType];

  const sourcePreviewPayload = useMemo(
    () =>
      buildSourceGenerationPayload({
        imageUrls: activeClips.map((clip) => clip.sourceImageUrl ?? clip.uploadedUrl ?? `pending://${clip.file.name}`),
        endImageUrls: activeClips.map((clip, index) => resolveEndImageUrlForPreview(clip, index)),
        cutPrompts: activeClips.map((clip) => clip.prompt),
        targetDurationsSec: activeClips.map((clip) => clip.targetDurationSec),
        contentType,
        aspectRatio,
        globalPrompt,
        tone,
        platform,
        audience: "",
        goal,
        language: "한국어",
      }),
    [activeClips, aspectRatio, contentType, globalPrompt, goal, platform, tone],
  );

  useEffect(() => {
    const activeObjectUrls = new Set<string>();
    for (const clip of clips) {
      activeObjectUrls.add(clip.previewUrl);
      if (clip.endPreviewUrl) activeObjectUrls.add(clip.endPreviewUrl);
    }
    for (const objectUrl of Array.from(objectUrlsRef.current)) {
      if (!activeObjectUrls.has(objectUrl)) {
        URL.revokeObjectURL(objectUrl);
        objectUrlsRef.current.delete(objectUrl);
      }
    }
  }, [clips]);

  useEffect(() => () => {
    for (const objectUrl of objectUrlsRef.current) {
      URL.revokeObjectURL(objectUrl);
    }
    objectUrlsRef.current.clear();
  }, []);

  useEffect(() => {
    if (activeStep === 3 && !finalTitleEdited && !finalTitle.trim()) {
      setFinalTitle(buildDefaultFinalTitle());
    }
  }, [activeStep, finalTitle, finalTitleEdited, goal, platform, contentType, aspectRatio, activeClips.length]);

  function updateStatus(message: string, nextProgress: number) {
    setStatus(message);
    setProgress(Math.max(0, Math.min(100, nextProgress)));
  }

  function setClipOrder(nextClips: ClipDraft[]) {
    if (isSequenceLocked) return;
    setClips(nextClips.map((clip, index) => ({
      ...clip,
      order: index + 1,
      generationMode: index === nextClips.length - 1 && clip.generationMode === "NEXT_START_AS_END" ? "START_ONLY" : clip.generationMode,
    })));
  }

  function createPreviewUrl(file: File): string {
    const objectUrl = URL.createObjectURL(file);
    objectUrlsRef.current.add(objectUrl);
    return objectUrl;
  }

  function handleFiles(nextFiles: FileList | null) {
    const selected = Array.from(nextFiles ?? []);
    const incoming = selected.filter((file) => file.type.startsWith("image/"));
    const limited = [...clips, ...incoming.map((file, index) => ({
      clientImageId: makeId("image"),
      file,
      previewUrl: createPreviewUrl(file),
      generationMode: "START_ONLY" as const,
      order: clips.length + index + 1,
      prompt: "",
      targetDurationSec: defaultSourceDurationSec,
      attempts: [],
    }))].slice(0, 10);
    setClipOrder(limited);
    if (incoming.length !== selected.length) {
      setError("이미지 파일만 업로드할 수 있습니다.");
    } else if (clips.length + incoming.length > 10) {
      setError("공간 사진은 최대 10장까지 사용할 수 있습니다.");
    } else {
      setError("");
    }
  }

  function updateClip(clientImageId: string, patch: Partial<ClipDraft>) {
    setClips((current) => current.map((clip) => (clip.clientImageId === clientImageId ? { ...clip, ...patch } : clip)));
  }

  function updateEndFrame(clientImageId: string, file: File | null) {
    updateClip(clientImageId, file ? {
      endFile: file,
      endPreviewUrl: createPreviewUrl(file),
      endUploadedUrl: undefined,
      endImageUrl: undefined,
      endGenerationUrl: undefined,
      generationMode: "START_END",
    } : {
      endFile: undefined,
      endPreviewUrl: undefined,
      endUploadedUrl: undefined,
      endImageUrl: undefined,
      endGenerationUrl: undefined,
      generationMode: "START_ONLY",
    });
  }

  function updateEndFrameMode(clientImageId: string, generationMode: MarketingGenerationMode) {
    if (generationMode === "START_ONLY" || generationMode === "NEXT_START_AS_END") {
      updateClip(clientImageId, {
        endFile: undefined,
        endPreviewUrl: undefined,
        endUploadedUrl: undefined,
        endImageUrl: undefined,
        endGenerationUrl: undefined,
        generationMode,
      });
      return;
    }
    updateClip(clientImageId, { generationMode: "START_END" });
  }

  function resolveEndImageUrlForPreview(clip: ClipDraft, index: number): string | undefined {
    if (clip.generationMode === "NEXT_START_AS_END") {
      const nextClip = activeClips[index + 1];
      return nextClip?.sourceImageUrl ?? nextClip?.uploadedUrl ?? (nextClip ? `pending://${nextClip.file.name}` : undefined);
    }
    return clip.endImageUrl ?? clip.endUploadedUrl ?? (clip.endFile ? `pending://${clip.endFile.name}` : undefined);
  }

  function resolveEndPreviewUrl(clip: ClipDraft, index: number): string | undefined {
    if (clip.generationMode === "NEXT_START_AS_END") {
      return activeClips[index + 1]?.previewUrl;
    }
    return clip.endPreviewUrl ?? clip.endImageUrl;
  }

  function mergeAttempt(clientImageId: string, attempt: MarketingVideoAttempt) {
    setClips((current) =>
      current.map((clip) => {
        if (clip.clientImageId !== clientImageId) return clip;
        const attempts = clip.attempts.some((item) => item.attemptId === attempt.attemptId)
          ? clip.attempts.map((item) => (item.attemptId === attempt.attemptId ? attempt : item))
          : [...clip.attempts, attempt];
        return { ...clip, attempts };
      }),
    );
  }

  function applyReviewProgress(nextClips: ClipDraft[]) {
    const reviewableClips = nextClips.filter((item) => !item.isDeleted);
    if (reviewableClips.length === 0) {
      updateStatus("활성 clip이 없습니다.", 0);
      return;
    }
    const approvedCount = reviewableClips.filter((item) => Boolean(item.approvedAttemptId)).length;
    if (approvedCount === reviewableClips.length) {
      updateStatus("모든 활성 clip이 승인되었습니다. 최종 합치기로 진행할 수 있습니다.", 100);
      return;
    }
    updateStatus(`승인 진행 중 (${approvedCount}/${reviewableClips.length})`, Math.round((approvedCount / reviewableClips.length) * 100));
  }

  function updateClipsWithReviewProgress(updater: (current: ClipDraft[]) => ClipDraft[]) {
    setClips((current) => {
      const next = updater(current);
      applyReviewProgress(next);
      return next;
    });
  }

  function buildDefaultFinalTitle(): string {
    const goalPart = goal.trim();
    const contentTypeLabel = contentTypeOptions.find((option) => option.value === contentType)?.label ?? "Marketing Reel";
    const base = goalPart || `${contentTypeLabel} ${platform}`;
    return `${base} · ${aspectRatio} · ${activeClips.length} clips · ${formatDateTitlePart()}`;
  }

  function getReferenceAttempt(clip: MarketingReelClipDetail) {
    return (
      clip.attempts.find((attempt) => attempt.attempt_id === clip.approved_attempt_id && attempt.source_video_url) ??
      clip.attempts.find((attempt) => attempt.status === "COMPLETED" && attempt.source_video_url) ??
      clip.attempts.at(-1)
    );
  }

  function getSelectedReferenceClip() {
    if (!selectedReference) return undefined;
    const selectedClip = selectedReference.clips.find((clip) => clip.clip_id === selectedReferenceClipId);
    if (selectedClip && getReferenceAttempt(selectedClip)?.source_video_url) return selectedClip;
    return selectedReference.clips.find((clip) => {
      const attempt = getReferenceAttempt(clip);
      return Boolean(attempt?.source_video_url);
    }) ?? selectedReference.clips[0];
  }

  async function pollJob(jobId: string, phaseLabel: string, baseProgress: number, progressSpan: number): Promise<VideoJobState> {
    for (;;) {
      const state = await fetchVideoJobStatus(jobId);
      const upstreamProgress = typeof state.progress === "number" ? state.progress : 0;
      updateStatus(`${phaseLabel}: ${state.message ?? state.status ?? "Working"}`, baseProgress + (upstreamProgress / 100) * progressSpan);
      if (state.status === "COMPLETED" || state.status === "FAILED") return state;
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    }
  }

  async function persistAttempt(group: string, clip: ClipDraft, attempt: MarketingVideoAttempt, sourceJobItemIndex: number) {
    if (!clip.clipId) return;
    await createMarketingClipAttempt(group, {
      attempt_id: attempt.attemptId,
      clip_id: clip.clipId,
      source_job_id: attempt.sourceJobId,
      source_job_item_index: sourceJobItemIndex,
      prompt: attempt.prompt,
      duration_sec: attempt.durationSec,
      status: attempt.status,
      source_video_url: attempt.videoUrl,
      download_url: attempt.downloadUrl,
      error: attempt.error,
    });
  }

  async function startSourceGeneration() {
    setError("");
    setFinalUrl("");
    setFinalSaveError("");
    setLastFinalPersistPayload(null);
    setIsBusy(true);
    let createdGroupId = "";
    let sourceJobStarted = false;
    try {
      validateImageSelection(activeClips.map((clip) => clip.file));
      updateStatus("마케팅 비디오 그룹 생성 중...", 4);
      const group = await createMarketingReelGroup({
        globalPrompt,
        platform,
        tone,
        goal,
        clips: activeClips.map((clip) => ({
          clientImageId: clip.clientImageId,
          sourceImageUrl: "",
          endImageUrl: undefined,
          generationMode: toApiGenerationMode(clip.generationMode),
          order: clip.order,
          prompt: clip.prompt,
          durationSec: clip.targetDurationSec,
        })),
      });
      setGroupId(group.group_id);
      createdGroupId = group.group_id;
      const clipIdByClient = new Map(group.clips.map((clip) => [clip.client_image_id, clip.clip_id]));

      updateStatus("이미지 업로드 중...", 8);
      const clipsNeedingUpload = activeClips.filter((clip) => !clip.uploadedUrl);
      const uploadedAssets = clipsNeedingUpload.length > 0
        ? await uploadOutputImageAssets(clipsNeedingUpload.map((clip) => clip.file), {
          purpose: "marketing-kling",
          groupId: group.group_id,
          assetType: "images",
          imageRole: "start",
        })
        : [];
      const uploadedAssetByClientId = new Map(
        clipsNeedingUpload.map((clip, index) => [clip.clientImageId, uploadedAssets[index]]),
      );
      const clipsNeedingEndUpload = activeClips.filter((clip) => clip.generationMode === "START_END" && clip.endFile && !clip.endUploadedUrl && !clip.endImageUrl);
      const uploadedEndAssets = clipsNeedingEndUpload.length > 0
        ? await uploadOutputImageAssets(clipsNeedingEndUpload.map((clip) => clip.endFile as File), {
          purpose: "marketing-kling",
          groupId: group.group_id,
          assetType: "images",
          imageRole: "end",
        })
        : [];
      const uploadedEndAssetByClientId = new Map(
        clipsNeedingEndUpload.map((clip, index) => [clip.clientImageId, uploadedEndAssets[index]]),
      );
      const uploadedBase = activeClips.map((clip) => {
        const sourceAsset = uploadedAssetByClientId.get(clip.clientImageId);
        const endAsset = uploadedEndAssetByClientId.get(clip.clientImageId);
        const sourceImageUrl = clip.sourceImageUrl ?? clip.uploadedUrl ?? sourceAsset?.publicUrl;
        const sourceGenerationUrl = clip.sourceGenerationUrl ?? sourceAsset?.readUrl ?? sourceImageUrl;
        const endImageUrl = clip.endImageUrl ?? clip.endUploadedUrl ?? endAsset?.publicUrl;
        const endGenerationUrl = clip.endGenerationUrl ?? endAsset?.readUrl ?? endImageUrl;
        if (!sourceImageUrl) throw new Error("이미지 업로드 결과 URL이 없습니다.");
        if (clip.generationMode === "START_END" && !endImageUrl) throw new Error(`${clip.file.name}의 End Frame을 업로드하거나 Start only로 변경하세요.`);
        return {
          ...clip,
          uploadedUrl: sourceImageUrl,
          sourceImageUrl,
          sourceGenerationUrl,
          endUploadedUrl: endImageUrl,
          endImageUrl,
          endGenerationUrl,
          clipId: clipIdByClient.get(clip.clientImageId),
        };
      });
      const uploaded = uploadedBase.map((clip, index) => {
        if (clip.generationMode === "NEXT_START_AS_END") {
          const nextClip = uploadedBase[index + 1];
          if (!nextClip?.sourceImageUrl) throw new Error(`${clip.file.name}은 다음 Start Frame을 End Frame으로 사용할 수 없습니다.`);
          return {
            ...clip,
            endUploadedUrl: nextClip.sourceImageUrl,
            endImageUrl: nextClip.sourceImageUrl,
            endGenerationUrl: nextClip.sourceGenerationUrl ?? nextClip.sourceImageUrl,
            generationMode: "NEXT_START_AS_END" as const,
          };
        }
        if (clip.endImageUrl) {
          return { ...clip, generationMode: "START_END" as const };
        }
        return { ...clip, generationMode: "START_ONLY" as const };
      });
      const patchedSourceImages = await updateMarketingClipSourceImages(group.group_id, {
        clips: uploaded.map((clip) => ({
          clip_id: clip.clipId as string,
          source_image_url: clip.sourceImageUrl as string,
          end_image_url: clip.endImageUrl,
          generation_mode: toApiGenerationMode(clip.generationMode),
        })),
      });
      const patchedByClipId = new Map(patchedSourceImages.clips.map((clip) => [clip.clip_id, clip]));
      updateStatus(`이미지 업로드 완료 (${uploaded.length}개)`, 20);

      const withClipIds = uploaded.map((clip) => {
        const patched = clip.clipId ? patchedByClipId.get(clip.clipId) : undefined;
        return {
          ...clip,
          sourceImageUrl: patched?.source_image_url ?? clip.sourceImageUrl,
          uploadedUrl: patched?.source_image_url ?? clip.uploadedUrl,
          endImageUrl: patched?.end_image_url ?? clip.endImageUrl,
          endUploadedUrl: patched?.end_image_url ?? clip.endUploadedUrl,
          sourceGenerationUrl: clip.sourceGenerationUrl ?? patched?.source_image_url ?? clip.sourceImageUrl,
          endGenerationUrl: clip.endGenerationUrl ?? patched?.end_image_url ?? clip.endImageUrl,
          generationMode: clip.generationMode === "NEXT_START_AS_END" ? clip.generationMode : patched?.generation_mode ?? clip.generationMode,
        };
      });
      setClips(withClipIds);
      setActiveStep(2);

      const sourceJobId = await requestSourceGeneration(buildSourceGenerationPayload({
        imageUrls: withClipIds.map((clip) => (clip.sourceGenerationUrl ?? clip.sourceImageUrl) as string),
        endImageUrls: withClipIds.map((clip) => clip.endGenerationUrl ?? clip.endImageUrl),
        cutPrompts: withClipIds.map((clip) => clip.prompt),
        targetDurationsSec: withClipIds.map((clip) => clip.targetDurationSec),
        contentType,
        aspectRatio,
        globalPrompt,
        tone,
        platform,
        audience: "",
        goal,
        language: "한국어",
      }));
      sourceJobStarted = true;

      const runningAttempts = withClipIds.map((clip, index) => makeAttempt({
        clip,
        sourceJobId,
        sourceJobItemIndex: index,
        prompt: buildKlingPrompt(
          { cutPrompts: [], contentType, aspectRatio, globalPrompt, tone, platform, audience: "", goal, language: "한국어" },
          clip.prompt || "premium furniture reel",
          index,
        ),
        status: "RUNNING",
      }));
      const persistedAttemptIds = new Set<string>();
      for (let index = 0; index < withClipIds.length; index += 1) {
        mergeAttempt(withClipIds[index].clientImageId, runningAttempts[index]);
        try {
          await persistAttempt(group.group_id, withClipIds[index], runningAttempts[index], index);
          persistedAttemptIds.add(runningAttempts[index].attemptId);
        } catch {
          // If the RUNNING row fails to persist, the final state is created after polling.
        }
      }

      const state = await pollJob(sourceJobId, "Kling source 생성", 25, 65);
      const errorsByIndex = new Map((state.errors ?? []).map((item) => [item.index, item.error ?? "Failed"]));
      const attemptSaveErrors: string[] = [];
      for (let index = 0; index < withClipIds.length; index += 1) {
        const localVideoUrl = state.results?.[index] ?? undefined;
        let videoUrl: string | undefined;
        let publishError: string | undefined;
        if (localVideoUrl) {
          try {
            videoUrl = await publishOutputAsset(localVideoUrl, {
              purpose: "marketing-kling",
              groupId: group.group_id,
              assetType: "videos",
            });
          } catch (caught) {
            publishError = caught instanceof Error ? caught.message : String(caught);
          }
        }
        const completed: MarketingVideoAttempt = {
          ...runningAttempts[index],
          status: videoUrl ? "COMPLETED" : "FAILED",
          videoUrl,
          downloadUrl: videoUrl ? downloadUrlForResult(videoUrl) : undefined,
          error: videoUrl ? undefined : publishError ?? errorsByIndex.get(index) ?? state.error ?? "Kling 생성 실패",
        };
        try {
          if (persistedAttemptIds.has(completed.attemptId)) {
            await updateMarketingClipAttempt(group.group_id, completed.attemptId, {
              status: completed.status,
              source_video_url: completed.videoUrl,
              download_url: completed.downloadUrl,
              error: completed.error,
            });
          } else {
            await persistAttempt(group.group_id, withClipIds[index], completed, index);
          }
          mergeAttempt(withClipIds[index].clientImageId, completed);
        } catch (caught) {
          const message = caught instanceof Error ? caught.message : String(caught);
          attemptSaveErrors.push(`Clip ${index + 1}: ${message}`);
          mergeAttempt(withClipIds[index].clientImageId, {
            ...completed,
            status: "FAILED",
            videoUrl: undefined,
            downloadUrl: undefined,
            error: `attempt 저장 실패: ${message}`,
          });
        }
      }
      if (attemptSaveErrors.length > 0) {
        setError(`일부 attempt 저장 실패: ${attemptSaveErrors.join(", ")}`);
      }
      updateStatus("비디오 확인 단계입니다. 좋은 결과를 승인하고 부족한 컷은 재생성하세요.", 100);
    } catch (caught) {
      if (createdGroupId && !sourceJobStarted) {
        try {
          await markMarketingReelGroupFailed(createdGroupId);
        } catch {
          // The original generation error is more useful to the operator here.
        }
        setGroupId("");
        setClips((current) => current.map((clip) => ({
          ...clip,
          clipId: undefined,
          sourceImageUrl: undefined,
          sourceGenerationUrl: undefined,
          uploadedUrl: undefined,
          endImageUrl: undefined,
          endGenerationUrl: undefined,
          endUploadedUrl: undefined,
        })));
      }
      setError(caught instanceof Error ? caught.message : String(caught));
      updateStatus(sourceJobStarted ? "생성 중 오류" : "생성 준비 실패", sourceJobStarted ? progress : 0);
    } finally {
      setIsBusy(false);
    }
  }

  async function approveAttempt(clip: ClipDraft, attempt: MarketingVideoAttempt) {
    if (!groupId || !clip.clipId || attempt.status !== "COMPLETED") return;
    setError("");
    try {
      await approveMarketingClipAttempt(groupId, clip.clipId, { attempt_id: attempt.attemptId });
      updateClipsWithReviewProgress((current) =>
        current.map((item) => (item.clientImageId === clip.clientImageId ? { ...item, approvedAttemptId: attempt.attemptId } : item)),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "저장 실패, 다시 승인 필요");
    }
  }

  async function deleteClipFromSequence(clip: ClipDraft) {
    setError("");
    if (groupId && clip.clipId) {
      try {
        await deleteMarketingReelClip(groupId, clip.clipId);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "clip 삭제 실패");
        return;
      }
    }
    updateClipsWithReviewProgress((current) =>
      current.map((item) => (item.clientImageId === clip.clientImageId ? { ...item, isDeleted: true, approvedAttemptId: undefined } : item)),
    );
  }

  async function regenerateClip(clip: ClipDraft) {
    const sourceImageUrl = clip.sourceImageUrl ?? clip.uploadedUrl;
    if (!groupId || !clip.clipId || !sourceImageUrl) return;
    const sourceGenerationUrl = clip.sourceGenerationUrl ?? sourceImageUrl;
    const endGenerationUrl = clip.endGenerationUrl ?? clip.endImageUrl;
    if (requiresEndFrame(clip.generationMode) && !clip.endImageUrl) {
      setError("Start/End Frame 컷은 End Frame 업로드와 DB 저장이 완료된 뒤 재생성할 수 있습니다.");
      return;
    }
    const prompt = regeneratePrompt || clip.prompt;
    setRegeneratingClipIds((current) => (current.includes(clip.clientImageId) ? current : [...current, clip.clientImageId]));
    setError("");
    try {
      const sourceJobId = await requestSourceGeneration(buildSourceGenerationPayload({
        imageUrls: [sourceGenerationUrl],
        endImageUrls: [endGenerationUrl],
        cutPrompts: [prompt],
        targetDurationsSec: [clip.targetDurationSec],
        contentType,
        aspectRatio,
        globalPrompt,
        tone,
        platform,
        audience: "",
        goal,
        language: "한국어",
      }));
      const attempt = makeAttempt({
        clip,
        sourceJobId,
        sourceJobItemIndex: 0,
        prompt,
        status: "RUNNING",
      });
      mergeAttempt(clip.clientImageId, attempt);
      await persistAttempt(groupId, clip, attempt, 0);
      const state = await pollJob(sourceJobId, "선택 컷 재생성", 20, 70);
      const localVideoUrl = state.results?.[0] ?? undefined;
      let videoUrl: string | undefined;
      let publishError: string | undefined;
      if (localVideoUrl) {
        try {
          videoUrl = await publishOutputAsset(localVideoUrl, {
            purpose: "marketing-kling",
            groupId,
            assetType: "videos",
          });
        } catch (caught) {
          publishError = caught instanceof Error ? caught.message : String(caught);
        }
      }
      const completed: MarketingVideoAttempt = {
        ...attempt,
        status: videoUrl ? "COMPLETED" : "FAILED",
        videoUrl,
        downloadUrl: videoUrl ? downloadUrlForResult(videoUrl) : undefined,
        error: videoUrl ? undefined : publishError ?? state.errors?.[0]?.error ?? state.error ?? "재생성 실패",
      };
      mergeAttempt(clip.clientImageId, completed);
      await updateMarketingClipAttempt(groupId, completed.attemptId, {
        status: completed.status,
        source_video_url: completed.videoUrl,
        download_url: completed.downloadUrl,
        error: completed.error,
      });
      if (regeneratingClipId === clip.clientImageId) {
        setRegeneratingClipId("");
        setRegeneratePrompt("");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRegeneratingClipIds((current) => current.filter((clientImageId) => clientImageId !== clip.clientImageId));
    }
  }

  async function compileFinal() {
    if (!groupId || !canCompile) return;
    setIsBusy(true);
    setError("");
    setFinalSaveError("");
    setActiveStep(3);
    try {
      const compilePayload = buildCompilePayloadFromApprovedItems(activeClips, aspectRatio);
      updateStatus("최종 영상 합치기 요청 중...", 20);
      const compileJobId = await requestCompile(compilePayload);
      const state = await pollJob(compileJobId, "최종 영상 합치기", 20, 70);
      if (!state.result_url) throw new Error(state.error ?? "최종 릴스 URL이 없습니다.");
      const finalVideoUrl = await publishOutputAsset(state.result_url, {
        purpose: "marketing-kling",
        groupId,
        assetType: "final",
      });
      setFinalUrl(finalVideoUrl);
      const persistPayload: MarketingFinalResultPayload = {
        compile_job_id: compileJobId,
        final_video_url: finalVideoUrl,
        final_download_url: downloadUrlForResult(finalVideoUrl),
        final_title: finalTitle.trim() || buildDefaultFinalTitle(),
        compile_payload_summary: compilePayload,
      };
      setLastFinalPersistPayload(persistPayload);
      try {
        await patchMarketingFinalResult(groupId, persistPayload);
        setLastFinalPersistPayload(null);
      } catch (caught) {
        setFinalSaveError(caught instanceof Error ? caught.message : "히스토리 저장 실패");
      }
      updateStatus("최종 릴스가 준비되었습니다.", 100);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsBusy(false);
    }
  }

  async function loadHistory() {
    try {
      setHistoryItems(await listMarketingReelGroups(20));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function retryFinalSave() {
    if (!groupId || !lastFinalPersistPayload) return;
    setFinalSaveError("");
    try {
      await patchMarketingFinalResult(groupId, lastFinalPersistPayload);
      setLastFinalPersistPayload(null);
      updateStatus("최종 릴스 히스토리 저장이 완료되었습니다.", 100);
    } catch (caught) {
      setFinalSaveError(caught instanceof Error ? caught.message : "히스토리 저장 실패");
    }
  }

  async function selectReference(group: MarketingReelGroupListItem) {
    try {
      const detail = await getMarketingReelGroup(group.group_id);
      setSelectedReference(detail);
      setHistoryTitleDraft(detail.final_title || "");
      setSelectedReferenceClipId("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function saveSelectedReferenceTitle() {
    if (!selectedReference) return;
    const title = historyTitleDraft.trim();
    if (!title) {
      setError("히스토리 제목을 입력하세요.");
      return;
    }
    setHistoryTitleSaving(true);
    setError("");
    try {
      const response = await updateMarketingReelGroupTitle(selectedReference.group_id, title);
      setSelectedReference({ ...selectedReference, final_title: response.final_title });
      setHistoryItems((current) =>
        current.map((item) => (item.group_id === selectedReference.group_id ? { ...item, final_title: response.final_title } : item)),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "히스토리 제목 수정 실패");
    } finally {
      setHistoryTitleSaving(false);
    }
  }

  const selectedReferenceClip = getSelectedReferenceClip();
  const selectedReferenceAttempt = selectedReferenceClip ? getReferenceAttempt(selectedReferenceClip) : undefined;

  return (
    <main className="studio-shell marketing-workflow-shell">
      <section className="marketing-tool-header">
        <div>
          <p className="marketing-kicker">Marketing Studio</p>
          <h1>Marketing Reels Studio</h1>
          <p>이미지별 Kling 결과를 검수하고 승인본만 합치는 운영팀용 3-step 제작 도구입니다.</p>
        </div>
        <button className="history-toggle" type="button" onClick={() => setHistoryOpen(!historyOpen)}>
          {historyOpen ? "히스토리 닫기" : "히스토리 열기"}
        </button>
      </section>

      <nav className="marketing-stepper" aria-label="Marketing reels steps">
        {(["생성 전", "비디오 확인", "최종 합치기"] as const).map((label, index) => {
          const step = (index + 1) as Step;
          return (
            <button
              key={label}
              className={activeStep === step ? "is-active" : ""}
              type="button"
              disabled={(step === 2 || step === 3) && !hasStartedGeneration}
              onClick={() => setActiveStep(step)}
            >
              <span>{step}</span>{label}
            </button>
          );
        })}
      </nav>

      {error ? <p className="marketing-alert" role="alert">{error}</p> : null}

      <section className={`marketing-workflow-grid ${historyOpen ? "has-history" : ""}`}>
        <div className="marketing-workspace">
          {activeStep === 1 ? (
            <section className="marketing-panel workflow-panel">
              <header className="workflow-panel-header">
                <div>
                  <h2>1. 생성 전</h2>
                  <p>공간 사진을 올리고 이미지별 prompt와 source video 길이를 설정합니다.</p>
                </div>
                <button className="run-reel-btn" type="button" disabled={isBusy || isSequenceLocked || activeClips.length < 3} onClick={startSourceGeneration}>
                  1차 비디오 생성
                </button>
              </header>
              {isSequenceLocked ? (
                <div className="marketing-alert sequence-lock-note" role="status">
                  생성 그룹이 만들어진 뒤에는 Step 1의 이미지 순서와 삭제를 변경하지 않습니다. 삭제나 재생성은 Step 2에서 처리하세요.
                </div>
              ) : null}

              <div className="brief-grid workflow-settings">
                <label className="brief-field">
                  <span className="brief-label">Content type</span>
                  <select className="brief-select" aria-label="Content type" value={contentType} disabled={isSequenceLocked} onChange={(event) => setContentType(event.target.value as ContentType)}>
                    {contentTypeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </label>
                <label className="brief-field">
                  <span className="brief-label">Tone</span>
                  <select className="brief-select" value={tone} disabled={isSequenceLocked} onChange={(event) => setTone(event.target.value)}>
                    <option>Editorial</option><option>Energetic</option><option>Calm</option><option>Bold</option>
                  </select>
                </label>
                <label className="brief-field">
                  <span className="brief-label">Platform</span>
                  <select className="brief-select" value={platform} disabled={isSequenceLocked} onChange={(event) => setPlatform(event.target.value)}>
                    <option>Instagram</option><option>TikTok</option><option>YouTube Shorts</option>
                  </select>
                </label>
                <label className="brief-field">
                  <span className="brief-label">Video ratio</span>
                  <select className="brief-select" value={aspectRatio} disabled={isSequenceLocked} onChange={(event) => setAspectRatio(normalizeMarketingAspectRatio(event.target.value))}>
                    {allowedMarketingAspectRatios.map((ratio) => <option key={ratio} value={ratio}>{aspectRatioLabels[ratio]}</option>)}
                  </select>
                </label>
                <label className="brief-field">
                  <span className="brief-label">Goal</span>
                  <input className="brief-input" value={goal} disabled={isSequenceLocked} onChange={(event) => setGoal(event.target.value)} />
                </label>
                <label className="brief-field is-wide">
                  <span className="brief-label">Global prompt</span>
                  <textarea className="brief-textarea" value={globalPrompt} disabled={isSequenceLocked} onChange={(event) => setGlobalPrompt(event.target.value)} />
                </label>
                <label className="brief-field is-wide upload-field">
                  <span className="brief-label">Images</span>
                  <span className="upload-count">{activeClips.length} / 10 selected</span>
                  <input aria-label="이미지 선택" type="file" accept="image/*" multiple disabled={isSequenceLocked} onChange={(event) => handleFiles(event.currentTarget.files)} />
                </label>
              </div>

              <div className="image-editor-list">
                {activeClips.length === 0 ? (
                  <div className="empty-state">아직 선택된 사진이 없습니다.</div>
                ) : activeClips.map((clip, index) => (
                  <article className="image-editor-card" key={clip.clientImageId}>
                    <div className="frame-pair-editor">
                      <figure>
                        <span>Start Frame</span>
                        <img src={clip.previewUrl} alt={`${index + 1}번 Start Frame 미리보기`} />
                      </figure>
                      <figure>
                        <span>End Frame</span>
                        {resolveEndPreviewUrl(clip, index) ? (
                          <img src={resolveEndPreviewUrl(clip, index)} alt={`${index + 1}번 End Frame 미리보기`} />
                        ) : (
                          <div className="frame-empty">Optional</div>
                        )}
                        {clip.generationMode !== "NEXT_START_AS_END" ? (
                          <button
                            className="frame-next-button"
                            type="button"
                            disabled={isSequenceLocked || index === activeClips.length - 1}
                            onClick={() => updateEndFrameMode(clip.clientImageId, "NEXT_START_AS_END")}
                          >
                            다음 Start 사용
                          </button>
                        ) : null}
                        <label className="frame-file-button">
                          End Frame 선택
                          <input
                            aria-label={`${index + 1}번 End Frame 선택`}
                            type="file"
                            accept="image/*"
                            disabled={isSequenceLocked || clip.generationMode === "NEXT_START_AS_END"}
                            onChange={(event) => updateEndFrame(clip.clientImageId, event.currentTarget.files?.[0] ?? null)}
                          />
                        </label>
                        {resolveEndPreviewUrl(clip, index) ? (
                          <button className="frame-clear-button" type="button" disabled={isSequenceLocked} onClick={() => updateEndFrame(clip.clientImageId, null)}>
                            제거
                          </button>
                        ) : null}
                      </figure>
                    </div>
                    <div>
                      <header>
                        <b>{index + 1}. {clip.file.name}</b>
                        <span>{generationModeLabel(clip.generationMode)} · {aspectRatio} · {clip.targetDurationSec}s</span>
                      </header>
                      <textarea
                        className="brief-textarea"
                        placeholder="이 이미지로 만들고 싶은 장면을 입력하세요."
                        value={clip.prompt}
                        disabled={isSequenceLocked}
                        onChange={(event) => updateClip(clip.clientImageId, { prompt: event.target.value })}
                      />
                      <div className="clip-controls">
                        <select
                          className="brief-select"
                          aria-label={`${index + 1}번 이미지 duration`}
                          value={clip.targetDurationSec}
                          disabled={isSequenceLocked}
                          onChange={(event) => updateClip(clip.clientImageId, { targetDurationSec: normalizeSourceDurationSec(event.target.value) })}
                        >
                          {allowedSourceDurationsSec.map((duration) => <option key={duration} value={duration}>{duration}초</option>)}
                        </select>
                        <button type="button" disabled={isSequenceLocked || index === 0} onClick={() => setClipOrder(moveImage(activeClips, index, -1))}>위</button>
                        <button type="button" disabled={isSequenceLocked || index === activeClips.length - 1} onClick={() => setClipOrder(moveImage(activeClips, index, 1))}>아래</button>
                        <button type="button" disabled={isSequenceLocked} onClick={() => setClipOrder(removeImage(activeClips, index))}>삭제</button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ) : null}

          {activeStep === 2 ? (
            <section className="marketing-panel workflow-panel">
              <header className="workflow-panel-header">
                <div>
                  <h2>2. 비디오 확인</h2>
                  <p>각 이미지의 생성 결과를 재생하고 좋은 attempt를 승인합니다.</p>
                </div>
                <button className="run-reel-btn" type="button" disabled={!canCompile || isBusy} onClick={() => setActiveStep(3)}>
                  승인본으로 합치기 준비
                </button>
              </header>

              <div className="progress-track" aria-label="Generation progress"><div className="progress-bar" style={{ width: `${progress}%` }} /></div>
              <p className="status-line">{status}</p>

              <div className="clip-review-list">
                {activeClips.map((clip, index) => {
                  const selectedAttempt =
                    clip.attempts.find((attempt) => attempt.attemptId === clip.viewingAttemptId) ??
                    clip.attempts.find((attempt) => attempt.attemptId === clip.approvedAttemptId) ??
                    clip.attempts.at(-1);
                  const isRegenerating = regeneratingClipId === clip.clientImageId;
                  const isClipRegenerating = regeneratingClipIds.includes(clip.clientImageId);
                  return (
                    <article className="clip-review-card" key={clip.clientImageId}>
                      <div className={`clip-review-media ratio-${aspectRatio.replace(":", "-")}`}>
                        <div className="review-frame-stack">
                          <figure>
                            <span>Start Frame</span>
                            <img src={clip.previewUrl} alt={`${index + 1}번 Start Frame`} />
                          </figure>
                          {resolveEndPreviewUrl(clip, index) ? (
                            <figure>
                              <span>{clip.generationMode === "NEXT_START_AS_END" ? "End Frame (Next Start)" : "End Frame"}</span>
                              <img src={resolveEndPreviewUrl(clip, index)} alt={`${index + 1}번 End Frame`} />
                            </figure>
                          ) : null}
                        </div>
                        {selectedAttempt?.videoUrl ? <video src={selectedAttempt.videoUrl} controls playsInline /> : <div className="video-placeholder">{selectedAttempt?.status ?? "대기"}</div>}
                      </div>
                      <div className="clip-review-body">
                        <header>
                          <h3>Clip {index + 1}</h3>
                          <span className={`status-badge status-${(selectedAttempt?.status ?? "QUEUED").toLowerCase()}`}>
                            {clip.approvedAttemptId ? "승인됨" : selectedAttempt?.status ?? "대기"}
                          </span>
                        </header>
                        <div className="clip-review-meta" aria-label={`Clip ${index + 1} 생성 설정`}>
                          <span>{generationModeLabel(clip.generationMode)}</span>
                          <span>{aspectRatioLabels[aspectRatio]}</span>
                          <span>{clip.targetDurationSec}초</span>
                        </div>
                        <p>{selectedAttempt?.prompt || clip.prompt || "프롬프트 없음"}</p>
                        <div className="clip-actions">
                          {selectedAttempt?.downloadUrl ? <a href={selectedAttempt.downloadUrl} download>다운로드</a> : null}
                          <button
                            className={clip.approvedAttemptId === selectedAttempt?.attemptId ? "is-approved" : ""}
                            type="button"
                            disabled={!selectedAttempt || selectedAttempt.status !== "COMPLETED"}
                            onClick={() => selectedAttempt && approveAttempt(clip, selectedAttempt)}
                          >
                            {clip.approvedAttemptId === selectedAttempt?.attemptId ? "승인됨" : "승인"}
                          </button>
                          <button type="button" onClick={() => { setRegeneratingClipId(clip.clientImageId); setRegeneratePrompt(selectedAttempt?.prompt ?? clip.prompt); }}>재생성</button>
                          <button type="button" disabled={isBusy} onClick={() => deleteClipFromSequence(clip)}>순서에서 삭제</button>
                        </div>
                        <div className="attempt-list">
                          {clip.attempts.map((attempt, attemptIndex) => (
                            <button
                              key={attempt.attemptId}
                              className={attempt.attemptId === selectedAttempt?.attemptId ? "is-selected" : ""}
                              type="button"
                              onClick={() => updateClip(clip.clientImageId, { viewingAttemptId: attempt.attemptId })}
                            >
                              #{attemptIndex + 1} {attempt.status} {attempt.attemptId === clip.approvedAttemptId ? "(approved)" : ""}
                            </button>
                          ))}
                        </div>
                        {isRegenerating ? (
                          <section className="regeneration-panel">
                            <h4>재생성</h4>
                            <div className="reference-grid">
                              <article><b>직전 attempt</b><p>{selectedAttempt?.prompt ?? "없음"}</p></article>
                              <article>
                                <b>선택한 레퍼런스</b>
                                {selectedReferenceClip ? (
                                  <>
                                    <p>{selectedReferenceClip.initial_prompt || selectedReference?.global_prompt}</p>
                                    {selectedReferenceAttempt?.source_video_url ? (
                                      <video src={selectedReferenceAttempt.source_video_url} controls playsInline />
                                    ) : null}
                                  </>
                                ) : (
                                  <p>히스토리에서 선택하세요.</p>
                                )}
                              </article>
                            </div>
                            <textarea className="brief-textarea" value={regeneratePrompt} onChange={(event) => setRegeneratePrompt(event.target.value)} />
                            <button className="run-reel-btn" type="button" disabled={isClipRegenerating} onClick={() => regenerateClip(clip)}>
                              {isClipRegenerating ? "재생성 중..." : "이 prompt로 재생성"}
                            </button>
                          </section>
                        ) : null}
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          ) : null}

          {activeStep === 3 ? (
            <section className="marketing-panel workflow-panel">
              <header className="workflow-panel-header">
                <div>
                  <h2>3. 최종 합치기</h2>
                  <p>승인한 source clips를 현재 순서대로 하나의 최종 영상으로 합칩니다.</p>
                </div>
                <button className="run-reel-btn" type="button" disabled={!canCompile || isBusy} onClick={compileFinal}>최종 영상 합치기</button>
              </header>
              {blockers.length ? (
                <div className="marketing-alert" role="alert">
                  아직 승인되지 않은 clip이 있습니다: {blockers.map((clip) => clip.file.name).join(", ")}
                </div>
              ) : null}
              <div className="progress-track" aria-label="Final merge progress"><div className="progress-bar" style={{ width: `${progress}%` }} /></div>
              <p className="status-line">{status}</p>
              <label className="brief-field final-title-field">
                <span className="brief-label">History title</span>
                <input
                  className="brief-input"
                  value={finalTitle}
                  placeholder="예: 호텔무드 침실 릴스 1차"
                  onChange={(event) => {
                    setFinalTitleEdited(true);
                    setFinalTitle(event.target.value);
                  }}
                />
              </label>
              <div className="approved-sequence">
                {activeClips.map((clip, index) => {
                  const approved = clip.attempts.find((attempt) => attempt.attemptId === clip.approvedAttemptId);
                  return <div className="clip-row" key={clip.clientImageId}><span>{index + 1}. {clip.file.name}</span><span>{approved?.videoUrl ? "승인 완료" : "미승인"}</span></div>;
                })}
              </div>
              {finalUrl ? (
                <section className="final-reel" aria-label="Final Reel">
                  <h3>Final Reel</h3>
                  <video className={`final-video ratio-${aspectRatio.replace(":", "-")}`} src={finalUrl} controls playsInline />
                  <a className="download-link" href={downloadUrlForResult(finalUrl)} download>Download final MP4</a>
                  {finalSaveError ? (
                    <div className="marketing-alert final-save-alert" role="alert">
                      <span>히스토리 저장 실패: {finalSaveError}</span>
                      <button type="button" onClick={retryFinalSave}>저장 재시도</button>
                    </div>
                  ) : null}
                </section>
              ) : null}
            </section>
          ) : null}
        </div>

        {historyOpen ? (
          <aside className="marketing-panel history-panel">
            <header>
              <h3>공용 히스토리</h3>
              <button type="button" onClick={loadHistory}>새로고침</button>
            </header>
            <div className="history-list">
              {historyItems.length === 0 ? <p className="status-line">저장된 결과물을 불러오세요.</p> : historyItems.map((item) => (
                <button key={item.group_id} type="button" onClick={() => selectReference(item)}>
                  <span>{item.status}</span>
                  <b>{item.final_title || `${item.clip_count} clips`}</b>
                  {item.final_title ? <small>{item.clip_count} clips</small> : null}
                  <small>{item.created_at}</small>
                </button>
              ))}
            </div>
            {selectedReference ? (
              <section className="history-detail">
                <h4>선택한 레퍼런스</h4>
                <div className="history-title-editor">
                  <label className="brief-field">
                    <span className="brief-label">History title</span>
                    <input
                      className="brief-input"
                      value={historyTitleDraft}
                      placeholder="히스토리 제목"
                      onChange={(event) => setHistoryTitleDraft(event.target.value)}
                    />
                  </label>
                  <button type="button" disabled={historyTitleSaving} onClick={saveSelectedReferenceTitle}>
                    {historyTitleSaving ? "저장 중..." : "제목 저장"}
                  </button>
                </div>
                <p>{selectedReference.global_prompt}</p>
                {selectedReference.final_video_url ? <video src={selectedReference.final_video_url} controls playsInline /> : null}
                <div className="history-reference-clips">
                  {selectedReference.clips.map((clip) => {
                    const referenceAttempt = getReferenceAttempt(clip);
                    const canSelectReferenceClip = Boolean(referenceAttempt?.source_video_url);
                    const isReferenceClipSelected = selectedReferenceClip?.clip_id === clip.clip_id;
                    return (
                      <article className={`history-reference-clip ${isReferenceClipSelected ? "is-selected" : ""}`} key={clip.clip_id}>
                        <header>
                          <b>Clip {clip.current_order}</b>
                          <span>{generationModeLabel(clip.generation_mode)}</span>
                        </header>
                        <button
                          className="reference-select-button"
                          type="button"
                          aria-label={`Clip ${clip.current_order} 레퍼런스로 선택`}
                          disabled={!canSelectReferenceClip}
                          onClick={() => setSelectedReferenceClipId(clip.clip_id)}
                        >
                          {canSelectReferenceClip ? (isReferenceClipSelected ? "선택됨" : "레퍼런스로 선택") : "비디오 없음"}
                        </button>
                        <div className="history-reference-media">
                          <figure>
                            <span>Start Frame</span>
                            <img src={clip.source_image_url} alt={`reference clip ${clip.current_order} start frame`} />
                          </figure>
                          {clip.end_image_url ? (
                            <figure>
                              <span>End Frame</span>
                              <img src={clip.end_image_url} alt={`reference clip ${clip.current_order} end frame`} />
                            </figure>
                          ) : null}
                        </div>
                        <p>{clip.initial_prompt || "프롬프트 없음"}</p>
                        {referenceAttempt ? (
                          <div className="history-reference-attempt">
                            <span>{referenceAttempt.status}</span>
                            <p>{referenceAttempt.prompt || "attempt prompt 없음"}</p>
                            {referenceAttempt.source_video_url ? <video src={referenceAttempt.source_video_url} controls playsInline /> : null}
                          </div>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              </section>
            ) : null}
            <details className="payload-preview">
              <summary>Kling payload preview</summary>
              <pre>{JSON.stringify(sourcePreviewPayload, null, 2)}</pre>
            </details>
            <div className="copy-stack">
              <article><h4>Hook</h4><p>{copy.hook}</p></article>
              <article><h4>Caption</h4><p>{copy.caption}</p></article>
              <article><h4>CTA</h4><p>{copy.cta}</p></article>
            </div>
          </aside>
        ) : null}
      </section>
    </main>
  );
}
