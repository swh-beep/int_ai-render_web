import { useEffect, useMemo, useRef, useState } from "react";

import {
  approveMarketingClipAttempt,
  createMarketingClipGeneration,
  createMarketingClipAttempt,
  createMarketingReelGroup,
  deleteAudioPrompt,
  deleteClipPrompt,
  deleteGlobalPrompt,
  deleteMarketingReelClip,
  getMarketingReelGroup,
  listAudioPrompts,
  listClipPrompts,
  listGlobalPrompts,
  listMarketingReelGroups,
  markMarketingReelGroupFailed,
  patchMarketingFinalResult,
  saveAudioPrompt,
  saveClipPrompt,
  saveGlobalPrompt,
  updateMarketingAudioSettings,
  updateMarketingReelGroupTitle,
  updateMarketingClipSourceImages,
  updateMarketingClipAttempt,
  type MarketingAudioPromptHistoryItem,
  type MarketingClipPromptHistoryItem,
  type MarketingFinalResultPayload,
  type MarketingGlobalPromptHistoryItem,
  type MarketingReelClipDetail,
  type MarketingReelGroupDetail,
  type MarketingReelGroupListItem,
  type MarketingReelAttemptDetail,
} from "../api/marketingReels";
import { publishOutputAsset, uploadOutputImageAssets } from "../api/outputs";
import {
  downloadUrlForResult,
  fetchVideoJobStatus,
  requestMarketingCompile,
  requestSourceGeneration,
  type VideoJobState,
} from "../api/videoMvp";
import {
  allowedSourceDurationsSec,
  allowedMarketingAspectRatioOptions,
  allowedMarketingVideoQualities,
  buildCompilePayloadFromApprovedItems,
  buildKlingPrompt,
  buildSourceGenerationPayload,
  defaultSourceDurationSec,
  defaultMarketingAspectRatio,
  defaultMarketingVideoQuality,
  generationModeLabel,
  getCompileBlockers,
  minimumImageCount,
  moveImage,
  normalizeGenerationAspectRatio,
  normalizeMarketingAspectRatio,
  normalizeMarketingVideoQuality,
  normalizeSourceDurationSec,
  removeImage,
  requiresEndFrame,
  type MarketingGenerationAspectRatio,
  type MarketingAspectRatio,
  type MarketingGenerationMode,
  type MarketingImageItem,
  type MarketingVideoQuality,
  type MarketingVideoAttempt,
  type SourceDurationSec,
  validateImageSelection,
} from "../domain/marketing";

type Step = 1 | 2 | 3;

type ClipDraft = MarketingImageItem & {
  previewUrl: string;
  sourcePreviewAspectRatio?: MarketingGenerationAspectRatio;
  endPreviewAspectRatio?: MarketingGenerationAspectRatio;
  clipId?: string;
  viewingAttemptId?: string;
  sourceGenerationUrl?: string;
  endGenerationUrl?: string;
};

type ClipPromptSourceItem = MarketingClipPromptHistoryItem & {
  source: "saved" | "history";
  sourceLabel: string;
  groupId?: string;
  clipId?: string;
};

type ReviewFramePreview = {
  id: string;
  clipIndex: number;
  frameType: "Start Frame" | "End Frame" | "End Frame (Next Start)";
  url: string;
};

type Step2Query = {
  path: "step2";
  id: string;
};

const aspectRatioLabels: Record<MarketingAspectRatio, string> = {
  source: "이미지 비율대로",
  "9:16": "9:16 세로",
  "16:9": "16:9 가로",
};
const videoQualityLabels: Record<MarketingVideoQuality, string> = {
  "720p": "720p",
  "1080p": "1080p",
};

const initialGlobalPrompt =
  "따뜻한 자연광 속에서 절제된 가구의 디테일을 보여주는 시네마틱 릴스. 부드러운 카메라 무빙, 베이지와 오크 톤.";
const initialAudioPrompt =
  "Generate natural motion-synced audio: subtle room tone and soft movement accents that follow the camera movement and scene transitions. Avoid narration, dialogue, loud music, and exaggerated sound effects. Keep the audio clean, premium, and unobtrusive.";
const initialMarketingStatus = "이미지 1~10장을 선택하고 1차 비디오 생성을 시작하세요.";

function formatDateTitlePart(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function formatHistoryDate(value: string | undefined): string {
  if (!value) return "날짜 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatHistoryRefreshTime(date: Date): string {
  return date.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function historyDisplayTitle(item: MarketingReelGroupListItem): string {
  return item.final_title?.trim() || `${item.clip_count} clips`;
}

function statusLabel(status: string): string {
  const normalized = status.toUpperCase();
  if (normalized === "COMPLETED") return "완료";
  if (normalized === "REVIEWING") return "검수";
  if (normalized === "GENERATING") return "생성중";
  if (normalized === "COMPILING") return "합치는중";
  if (normalized === "FAILED") return "실패";
  return status || "상태 없음";
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

function fileFromRemoteUrl(url: string, fallbackName: string): File {
  const name = decodeURIComponent(url.split("/").pop() || fallbackName);
  return new File([""], name, { type: "image/png" });
}

function attemptFromDetail(attempt: MarketingReelAttemptDetail): MarketingVideoAttempt {
  return {
    attemptId: attempt.attempt_id,
    sourceJobId: attempt.source_job_id,
    index: attempt.source_job_item_index,
    prompt: attempt.prompt,
    status: attempt.status,
    videoUrl: attempt.source_video_url,
    downloadUrl: attempt.download_url,
    durationSec: attempt.duration_sec,
    error: attempt.error,
    createdAt: attempt.created_at ?? new Date().toISOString(),
  };
}

function readStep2Query(): Step2Query | null {
  const params = new URLSearchParams(window.location.search);
  const path = params.get("path");
  const id = params.get("id")?.trim();
  if (path !== "step2" || !id) return null;
  return { path, id };
}

function writeStep2Query(groupId: string, mode: "push" | "replace") {
  const params = new URLSearchParams(window.location.search);
  params.set("path", "step2");
  params.set("id", groupId);
  const search = params.toString();
  window.history[mode === "push" ? "pushState" : "replaceState"](
    {},
    "",
    `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`,
  );
}

function clearMarketingQuery() {
  const params = new URLSearchParams(window.location.search);
  params.delete("path");
  params.delete("id");
  const search = params.toString();
  window.history.replaceState({}, "", `${window.location.pathname}${search ? `?${search}` : ""}${window.location.hash}`);
}

export function MarketingPage() {
  const objectUrlsRef = useRef(new Set<string>());
  const lastRestoredQueryIdRef = useRef("");
  const [activeStep, setActiveStep] = useState<Step>(1);
  const [aspectRatio, setAspectRatio] = useState<MarketingAspectRatio>(defaultMarketingAspectRatio);
  const [generationAspectRatio, setGenerationAspectRatio] = useState<MarketingGenerationAspectRatio>(defaultMarketingAspectRatio);
  const [videoQuality, setVideoQuality] = useState<MarketingVideoQuality>(defaultMarketingVideoQuality);
  const [clips, setClips] = useState<ClipDraft[]>([]);
  const [globalPrompt, setGlobalPrompt] = useState(initialGlobalPrompt);
  const [audioEnabled, setAudioEnabled] = useState(false);
  const [audioPrompt, setAudioPrompt] = useState(initialAudioPrompt);
  const [groupId, setGroupId] = useState("");
  const [status, setStatus] = useState(initialMarketingStatus);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState(0);
  const [isBusy, setIsBusy] = useState(false);
  const [regeneratingClipId, setRegeneratingClipId] = useState("");
  const [regeneratingClipIds, setRegeneratingClipIds] = useState<string[]>([]);
  const [regeneratePrompt, setRegeneratePrompt] = useState("");
  const [selectedReference, setSelectedReference] = useState<MarketingReelGroupDetail | null>(null);
  const [selectedReferenceClipId, setSelectedReferenceClipId] = useState("");
  const [historyItems, setHistoryItems] = useState<MarketingReelGroupListItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyFilter, setHistoryFilter] = useState<"all" | "final" | "review">("all");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyToast, setHistoryToast] = useState("");
  const [historyLastLoadedAt, setHistoryLastLoadedAt] = useState<Date | null>(null);
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [selectedReferenceId, setSelectedReferenceId] = useState("");
  const [finalUrl, setFinalUrl] = useState("");
  const [finalTitle, setFinalTitle] = useState("");
  const [finalTitleEdited, setFinalTitleEdited] = useState(false);
  const [historyTitleDraft, setHistoryTitleDraft] = useState("");
  const [historyTitleSaving, setHistoryTitleSaving] = useState(false);
  const [finalSaveError, setFinalSaveError] = useState("");
  const [lastFinalPersistPayload, setLastFinalPersistPayload] = useState<MarketingFinalResultPayload | null>(null);
  const [promptHistoryOpen, setPromptHistoryOpen] = useState(false);
  const [promptHistoryItems, setPromptHistoryItems] = useState<MarketingGlobalPromptHistoryItem[]>([]);
  const [promptHistoryStatus, setPromptHistoryStatus] = useState("");
  const [isPromptHistoryLoading, setIsPromptHistoryLoading] = useState(false);
  const [isPromptSaving, setIsPromptSaving] = useState(false);
  const [audioPromptHistoryOpen, setAudioPromptHistoryOpen] = useState(false);
  const [audioPromptItems, setAudioPromptItems] = useState<MarketingAudioPromptHistoryItem[]>([]);
  const [audioPromptStatus, setAudioPromptStatus] = useState("");
  const [isAudioPromptLoading, setIsAudioPromptLoading] = useState(false);
  const [isAudioPromptSaving, setIsAudioPromptSaving] = useState(false);
  const [clipPromptMode, setClipPromptMode] = useState<"save" | "load" | null>(null);
  const [clipPromptTargetId, setClipPromptTargetId] = useState("");
  const [clipPromptItems, setClipPromptItems] = useState<MarketingClipPromptHistoryItem[]>([]);
  const [historyClipPromptItems, setHistoryClipPromptItems] = useState<ClipPromptSourceItem[]>([]);
  const [clipPromptTab, setClipPromptTab] = useState<"saved" | "history">("saved");
  const [clipPromptStatus, setClipPromptStatus] = useState("");
  const [clipPromptTitle, setClipPromptTitle] = useState("");
  const [clipPromptQuery, setClipPromptQuery] = useState("");
  const [isClipPromptLoading, setIsClipPromptLoading] = useState(false);
  const [isClipPromptSaving, setIsClipPromptSaving] = useState(false);
  const [activeFramePreviewId, setActiveFramePreviewId] = useState<string | null>(null);
  const [isRestoringFromUrl, setIsRestoringFromUrl] = useState(() => Boolean(readStep2Query()));

  const activeClips = clips.filter((clip) => !clip.isDeleted);
  const blockers = getCompileBlockers(activeClips);
  const canCompile = activeClips.length > 0 && blockers.length === 0;
  const hasStartedGeneration = Boolean(groupId) || clips.some((clip) => clip.attempts.length > 0);
  const isSequenceLocked = Boolean(groupId);
  const displayAspectRatio = aspectRatio === "source" ? generationAspectRatio : aspectRatio;
  const filteredHistoryItems = useMemo(() => {
    const normalizedQuery = historyQuery.trim().toLowerCase();
    return historyItems.filter((item) => {
      const normalizedStatus = item.status.toUpperCase();
      const matchesFilter =
        historyFilter === "all" ||
        (historyFilter === "final" && Boolean(item.final_video_url)) ||
        (historyFilter === "review" && !item.final_video_url && ["REVIEWING", "GENERATING", "COMPILING"].includes(normalizedStatus));
      if (!matchesFilter) return false;
      if (!normalizedQuery) return true;
      return [
        historyDisplayTitle(item),
        item.status,
        statusLabel(item.status),
        item.created_at,
        String(item.clip_count),
      ].some((value) => value.toLowerCase().includes(normalizedQuery));
    });
  }, [historyFilter, historyItems, historyQuery]);
  const finalHistoryCount = historyItems.filter((item) => Boolean(item.final_video_url)).length;
  const reviewHistoryCount = historyItems.filter((item) => !item.final_video_url && ["REVIEWING", "GENERATING", "COMPILING"].includes(item.status.toUpperCase())).length;
  const selectedClipPromptTarget = activeClips.find((clip) => clip.clientImageId === clipPromptTargetId);
  const savedClipPromptItems: ClipPromptSourceItem[] = useMemo(() =>
    clipPromptItems.map((item) => ({
      ...item,
      source: "saved",
      sourceLabel: "저장한 Prompt",
    })),
  [clipPromptItems]);
  const visibleClipPromptItems = clipPromptTab === "saved" ? savedClipPromptItems : historyClipPromptItems;
  const filteredClipPromptItems = useMemo(() => {
    const normalizedQuery = clipPromptQuery.trim().toLowerCase();
    if (!normalizedQuery) return visibleClipPromptItems;
    return visibleClipPromptItems.filter((item) =>
      [item.title, item.prompt, item.created_at, item.sourceLabel].some((value) => value.toLowerCase().includes(normalizedQuery)),
    );
  }, [clipPromptQuery, visibleClipPromptItems]);
  const reviewFramePreviews = useMemo<ReviewFramePreview[]>(() => {
    return activeClips.flatMap((clip, index) => {
      const endPreviewUrl = resolveEndPreviewUrl(clip, index);
      const frames: ReviewFramePreview[] = [{
        id: `${clip.clientImageId}:start`,
        clipIndex: index,
        frameType: "Start Frame",
        url: clip.previewUrl,
      }];
      if (endPreviewUrl) {
        frames.push({
          id: `${clip.clientImageId}:end`,
          clipIndex: index,
          frameType: clip.generationMode === "NEXT_START_AS_END" ? "End Frame (Next Start)" : "End Frame",
          url: endPreviewUrl,
        });
      }
      return frames;
    });
  }, [activeClips]);
  const activeFramePreview = reviewFramePreviews.find((frame) => frame.id === activeFramePreviewId);
  const hasOpenModal = historyOpen || promptHistoryOpen || audioPromptHistoryOpen || Boolean(clipPromptMode) || Boolean(activeFramePreview);
  const hasWorkspaceState =
    activeStep !== 1 ||
    clips.length > 0 ||
    Boolean(groupId) ||
    Boolean(finalUrl) ||
    progress > 0 ||
    globalPrompt !== initialGlobalPrompt ||
    audioEnabled ||
    audioPrompt !== initialAudioPrompt;

  const sourcePreviewPayload = useMemo(
    () =>
      buildSourceGenerationPayload({
        imageUrls: activeClips.map((clip) => clip.sourceImageUrl ?? clip.uploadedUrl ?? `pending://${clip.file.name}`),
        endImageUrls: activeClips.map((clip, index) => resolveEndImageUrlForPreview(clip, index)),
        cutPrompts: activeClips.map((clip) => clip.prompt),
        targetDurationsSec: activeClips.map((clip) => clip.targetDurationSec),
        aspectRatio: displayAspectRatio,
        videoQuality,
        globalPrompt,
        audioEnabled,
        audioPrompt,
        language: "한국어",
      }),
    [activeClips, audioEnabled, audioPrompt, displayAspectRatio, globalPrompt, videoQuality],
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
  }, [activeStep, finalTitle, finalTitleEdited, displayAspectRatio, activeClips.length]);

  useEffect(() => {
    if (!hasOpenModal) return undefined;
    const previousOverflow = document.body.style.overflow;
    const previousPaddingRight = document.body.style.paddingRight;
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = "hidden";
    if (scrollbarWidth > 0) {
      document.body.style.paddingRight = `${scrollbarWidth}px`;
    }
    return () => {
      document.body.style.overflow = previousOverflow;
      document.body.style.paddingRight = previousPaddingRight;
    };
  }, [hasOpenModal]);

  useEffect(() => {
    if (!historyOpen) return undefined;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setHistoryOpen(false);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [historyOpen]);

  useEffect(() => {
    if (!activeFramePreview) return undefined;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setActiveFramePreviewId(null);
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        navigateFramePreview(event.key === "ArrowRight" ? 1 : -1);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeFramePreview, reviewFramePreviews]);

  useEffect(() => {
    if (!historyToast) return undefined;
    const timer = window.setTimeout(() => setHistoryToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [historyToast]);

  useEffect(() => {
    function restoreFromCurrentQuery(force = false) {
      const query = readStep2Query();
      if (!query) return;
      if (!force && lastRestoredQueryIdRef.current === query.id) return;
      lastRestoredQueryIdRef.current = query.id;
      setIsRestoringFromUrl(true);
      setStatus("저장된 Step 2를 불러오는 중...");
      setError("");
      void getMarketingReelGroup(query.id)
        .then((detail) => {
          restoreReferenceToStep2(detail, {
            closeHistory: false,
            statusMessage: "저장된 Step 2 결과를 불러왔습니다.",
          });
        })
        .catch((caught) => {
          setError(caught instanceof Error ? caught.message : "마케팅 릴스 상세 조회 실패");
          setActiveStep(1);
        })
        .finally(() => setIsRestoringFromUrl(false));
    }

    restoreFromCurrentQuery();
    const handlePopState = () => restoreFromCurrentQuery(true);
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  function updateStatus(message: string, nextProgress: number) {
    setStatus(message);
    setProgress(Math.max(0, Math.min(100, nextProgress)));
  }

  function navigateFramePreview(direction: -1 | 1) {
    if (!reviewFramePreviews.length) return;
    const currentIndex = reviewFramePreviews.findIndex((frame) => frame.id === activeFramePreviewId);
    const safeIndex = currentIndex >= 0 ? currentIndex : 0;
    const nextIndex = (safeIndex + direction + reviewFramePreviews.length) % reviewFramePreviews.length;
    setActiveFramePreviewId(reviewFramePreviews[nextIndex].id);
  }

  function showHistoryToast(message: string) {
    setHistoryToast(message);
  }

  function resetMarketingWorkspace() {
    clearMarketingQuery();
    setActiveStep(1);
    setAspectRatio(defaultMarketingAspectRatio);
    setGenerationAspectRatio(defaultMarketingAspectRatio);
    setClips([]);
    setGlobalPrompt(initialGlobalPrompt);
    setAudioEnabled(false);
    setAudioPrompt(initialAudioPrompt);
    setGroupId("");
    setStatus(initialMarketingStatus);
    setError("");
    setProgress(0);
    setIsBusy(false);
    setRegeneratingClipId("");
    setRegeneratingClipIds([]);
    setRegeneratePrompt("");
    setSelectedReference(null);
    setSelectedReferenceClipId("");
    setHistoryOpen(false);
    setHistoryQuery("");
    setHistoryFilter("all");
    setHistoryStatus("");
    setSelectedReferenceId("");
    setFinalUrl("");
    setFinalTitle("");
    setFinalTitleEdited(false);
    setHistoryTitleDraft("");
    setHistoryTitleSaving(false);
    setFinalSaveError("");
    setLastFinalPersistPayload(null);
    setPromptHistoryOpen(false);
    setAudioPromptHistoryOpen(false);
    setAudioPromptStatus("");
    setClipPromptMode(null);
    setClipPromptTargetId("");
    setHistoryClipPromptItems([]);
    setClipPromptTab("saved");
    setClipPromptStatus("");
    setClipPromptTitle("");
    setClipPromptQuery("");
    showHistoryToast("새 작업을 시작할 수 있도록 초기화했습니다.");
  }

  function restoreCurrentDataToStep1Draft() {
    clearMarketingQuery();
    setGroupId("");
    setFinalUrl("");
    setFinalTitle("");
    setFinalTitleEdited(false);
    setFinalSaveError("");
    setLastFinalPersistPayload(null);
    setProgress(0);
    setError("");
    setRegeneratingClipId("");
    setRegeneratingClipIds([]);
    setRegeneratePrompt("");
    setSelectedReference(null);
    setSelectedReferenceClipId("");
    setSelectedReferenceId("");
    setClips(activeClips.map((clip, index) => ({
      ...clip,
      clipId: undefined,
      order: index + 1,
      attempts: [],
      approvedAttemptId: undefined,
      viewingAttemptId: undefined,
      isDeleted: undefined,
    })));
    setActiveStep(1);
    setStatus("현재 데이터로 Step 1 설정을 수정할 수 있습니다.");
    showHistoryToast("현재 데이터로 새 작업을 시작합니다.");
  }

  function selectWorkflowStep(step: Step) {
    setActiveStep(step);
    if (step === 1 && hasStartedGeneration) {
      window.alert("이 데이터로 작업 시작 버튼 선택시 설정을 수정할 수 있습니다");
    }
  }

  async function resolveAspectRatioForGeneration(sourceClips: ClipDraft[] = activeClips): Promise<MarketingGenerationAspectRatio> {
    if (aspectRatio !== "source") return aspectRatio;
    const firstFile = sourceClips[0]?.file;
    if (!firstFile) return defaultMarketingAspectRatio;
    try {
      const bitmap = await createImageBitmap(firstFile);
      const resolved = bitmap.width >= bitmap.height ? "16:9" : "9:16";
      bitmap.close();
      return resolved;
    } catch {
      return new Promise((resolve) => {
        const objectUrl = URL.createObjectURL(firstFile);
        const image = new Image();
        image.onload = () => {
          URL.revokeObjectURL(objectUrl);
          resolve(image.naturalWidth >= image.naturalHeight ? "16:9" : "9:16");
        };
        image.onerror = () => {
          URL.revokeObjectURL(objectUrl);
          resolve(defaultMarketingAspectRatio);
        };
        image.src = objectUrl;
      });
    }
  }

  async function detectImageAspectRatio(file: File): Promise<MarketingGenerationAspectRatio> {
    try {
      const bitmap = await createImageBitmap(file);
      const resolved = bitmap.width >= bitmap.height ? "16:9" : "9:16";
      bitmap.close();
      return resolved;
    } catch {
      return new Promise((resolve) => {
        const objectUrl = URL.createObjectURL(file);
        const image = new Image();
        image.onload = () => {
          URL.revokeObjectURL(objectUrl);
          resolve(image.naturalWidth >= image.naturalHeight ? "16:9" : "9:16");
        };
        image.onerror = () => {
          URL.revokeObjectURL(objectUrl);
          resolve(defaultMarketingAspectRatio);
        };
        image.src = objectUrl;
      });
    }
  }

  function rememberSourcePreviewAspectRatio(clientImageId: string, file: File) {
    void detectImageAspectRatio(file).then((sourcePreviewAspectRatio) => {
      setClips((current) => current.map((clip) => {
        if (clip.clientImageId !== clientImageId || clip.file !== file) return clip;
        return { ...clip, sourcePreviewAspectRatio };
      }));
    });
  }

  function rememberEndPreviewAspectRatio(clientImageId: string, file: File) {
    void detectImageAspectRatio(file).then((endPreviewAspectRatio) => {
      setClips((current) => current.map((clip) => {
        if (clip.clientImageId !== clientImageId || clip.endFile !== file) return clip;
        return { ...clip, endPreviewAspectRatio };
      }));
    });
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
    const incomingClips = incoming.map((file, index) => ({
      clientImageId: makeId("image"),
      file,
      previewUrl: createPreviewUrl(file),
      generationMode: "START_ONLY" as const,
      order: clips.length + index + 1,
      prompt: "",
      targetDurationSec: defaultSourceDurationSec,
      attempts: [],
    }));
    const limited = [...clips, ...incomingClips].slice(0, 10);
    setClipOrder(limited);
    incomingClips.forEach((clip) => rememberSourcePreviewAspectRatio(clip.clientImageId, clip.file));
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
    if (file) {
      updateClip(clientImageId, {
        endFile: file,
        endPreviewUrl: createPreviewUrl(file),
        endPreviewAspectRatio: undefined,
        endUploadedUrl: undefined,
        endImageUrl: undefined,
        endGenerationUrl: undefined,
        generationMode: "START_END",
      });
      rememberEndPreviewAspectRatio(clientImageId, file);
      return;
    }
    updateClip(clientImageId, {
      endFile: undefined,
      endPreviewUrl: undefined,
      endPreviewAspectRatio: undefined,
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
        endPreviewAspectRatio: undefined,
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

  function resolveEndPreviewAspectRatio(clip: ClipDraft, index: number): MarketingGenerationAspectRatio | undefined {
    if (clip.generationMode === "NEXT_START_AS_END") {
      return activeClips[index + 1]?.sourcePreviewAspectRatio;
    }
    return clip.endPreviewAspectRatio;
  }

  function frameRatioClass(ratio?: MarketingGenerationAspectRatio) {
    return `ratio-${(ratio ?? displayAspectRatio).replace(":", "-")}`;
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
    return `Marketing Reel · ${aspectRatioLabels[displayAspectRatio]} · ${activeClips.length} clips · ${formatDateTitlePart()}`;
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

  async function persistAttempt(group: string, clip: ClipDraft, attempt: MarketingVideoAttempt, sourceJobItemIndex: number, clipGenerationId?: string) {
    if (!clip.clipId) return;
    await createMarketingClipAttempt(group, {
      attempt_id: attempt.attemptId,
      clip_id: clip.clipId,
      clip_generation_id: clipGenerationId,
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
      const resolvedAspectRatio = await resolveAspectRatioForGeneration(activeClips);
      setGenerationAspectRatio(resolvedAspectRatio);
      updateStatus("마케팅 비디오 그룹 생성 중...", 4);
      const group = await createMarketingReelGroup({
        globalPrompt,
        audioEnabled,
        audioPrompt,
        aspectRatio: resolvedAspectRatio,
        videoQuality,
        platform: "",
        tone: "",
        goal: "",
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
      writeStep2Query(group.group_id, "replace");

      const sourceJobId = await requestSourceGeneration(buildSourceGenerationPayload({
        imageUrls: withClipIds.map((clip) => (clip.sourceGenerationUrl ?? clip.sourceImageUrl) as string),
        endImageUrls: withClipIds.map((clip) => clip.endGenerationUrl ?? clip.endImageUrl),
        cutPrompts: withClipIds.map((clip) => clip.prompt),
        targetDurationsSec: withClipIds.map((clip) => clip.targetDurationSec),
        aspectRatio: resolvedAspectRatio,
        videoQuality,
        globalPrompt,
        audioEnabled,
        audioPrompt,
        language: "한국어",
      }));
      sourceJobStarted = true;
      const generation = await createMarketingClipGeneration(group.group_id, {
        generation_type: "INITIAL",
        clip_ids: withClipIds.map((clip) => clip.clipId as string),
        source_job_id: sourceJobId,
      });

      const runningAttempts = withClipIds.map((clip, index) => makeAttempt({
          clip,
          sourceJobId,
          sourceJobItemIndex: index,
          prompt: buildKlingPrompt(
          { cutPrompts: [], aspectRatio: resolvedAspectRatio, globalPrompt, audioEnabled, audioPrompt, language: "한국어" },
          clip.prompt || "premium furniture reel",
          index,
        ),
        status: "RUNNING",
      }));
      const persistedAttemptIds = new Set<string>();
      for (let index = 0; index < withClipIds.length; index += 1) {
        mergeAttempt(withClipIds[index].clientImageId, runningAttempts[index]);
        try {
          await persistAttempt(group.group_id, withClipIds[index], {
            ...runningAttempts[index],
            attemptId: runningAttempts[index].attemptId,
          }, index, generation.clip_generation_id);
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
            await persistAttempt(group.group_id, withClipIds[index], completed, index, generation.clip_generation_id);
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
      const resolvedAspectRatio = await resolveAspectRatioForGeneration([clip]);
      setGenerationAspectRatio(resolvedAspectRatio);
      const sourceJobId = await requestSourceGeneration(buildSourceGenerationPayload({
        imageUrls: [sourceGenerationUrl],
        endImageUrls: [endGenerationUrl],
        cutPrompts: [prompt],
        targetDurationsSec: [clip.targetDurationSec],
        aspectRatio: resolvedAspectRatio,
        videoQuality,
        globalPrompt,
        audioEnabled,
        audioPrompt,
        language: "한국어",
      }));
      const generation = await createMarketingClipGeneration(groupId, {
        generation_type: "REGENERATE",
        clip_ids: [clip.clipId],
        source_job_id: sourceJobId,
      });
      const attempt = makeAttempt({
        clip,
        sourceJobId,
        sourceJobItemIndex: 0,
        prompt,
        status: "RUNNING",
      });
      mergeAttempt(clip.clientImageId, attempt);
      await persistAttempt(groupId, clip, attempt, 0, generation.clip_generation_id);
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
      const resolvedAspectRatio = await resolveAspectRatioForGeneration(activeClips);
      setGenerationAspectRatio(resolvedAspectRatio);
      const compilePayload = buildCompilePayloadFromApprovedItems(activeClips, resolvedAspectRatio, videoQuality, audioEnabled);
      updateStatus("최종 영상 합치기 요청 중...", 20);
      const compileJobId = await requestMarketingCompile(compilePayload);
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
        selected_attempt_ids: activeClips
          .map((clip) => clip.approvedAttemptId)
          .filter((attemptId): attemptId is string => Boolean(attemptId)),
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
    setIsHistoryLoading(true);
    setHistoryStatus("");
    try {
      const nextItems = await listMarketingReelGroups(20);
      setHistoryItems(nextItems);
      setHistoryLastLoadedAt(new Date());
      setSelectedReference((current) => {
        if (!current) return current;
        return nextItems.some((item) => item.group_id === current.group_id) ? current : null;
      });
      setHistoryStatus(nextItems.length > 0 ? `${nextItems.length}개 히스토리를 불러왔습니다.` : "저장된 히스토리가 없습니다.");
    } catch (caught) {
      setHistoryStatus(caught instanceof Error ? caught.message : "히스토리 조회 실패");
    } finally {
      setIsHistoryLoading(false);
    }
  }

  function openHistoryModal() {
    setHistoryOpen(true);
    void loadHistory();
  }

  async function saveCurrentGlobalPrompt() {
    const prompt = globalPrompt.trim();
    if (!prompt) {
      setPromptHistoryStatus("저장할 Global prompt를 입력하세요.");
      return;
    }
    setIsPromptSaving(true);
    setPromptHistoryStatus("");
    try {
      await saveGlobalPrompt(prompt);
      setPromptHistoryStatus("Global prompt 저장 완료");
    } catch (caught) {
      setPromptHistoryStatus(caught instanceof Error ? caught.message : "Global prompt 저장 실패");
    } finally {
      setIsPromptSaving(false);
    }
  }

  async function openGlobalPromptHistory() {
    setPromptHistoryOpen(true);
    setPromptHistoryStatus("");
    setIsPromptHistoryLoading(true);
    try {
      setPromptHistoryItems(await listGlobalPrompts(30));
    } catch (caught) {
      setPromptHistoryStatus(caught instanceof Error ? caught.message : "Global prompt 내역 조회 실패");
    } finally {
      setIsPromptHistoryLoading(false);
    }
  }

  async function deletePromptHistoryItem(promptId: string) {
    setPromptHistoryStatus("");
    try {
      await deleteGlobalPrompt(promptId);
      setPromptHistoryItems((current) => current.filter((item) => item.id !== promptId));
      setPromptHistoryStatus("Global prompt 삭제 완료");
    } catch (caught) {
      setPromptHistoryStatus(caught instanceof Error ? caught.message : "Global prompt 삭제 실패");
    }
  }

  async function saveCurrentAudioPrompt() {
    const prompt = audioPrompt.trim();
    if (!prompt) {
      setAudioPromptStatus("저장할 음성 프롬프트를 입력하세요.");
      return;
    }
    setIsAudioPromptSaving(true);
    setAudioPromptStatus("");
    try {
      const title = prompt.length > 40 ? `${prompt.slice(0, 40)}...` : prompt;
      const saved = await saveAudioPrompt(title, prompt);
      setAudioPromptItems((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setAudioPromptStatus("음성 프롬프트 저장 완료");
    } catch (caught) {
      setAudioPromptStatus(caught instanceof Error ? caught.message : "음성 프롬프트 저장 실패");
    } finally {
      setIsAudioPromptSaving(false);
    }
  }

  async function openAudioPromptHistory() {
    setAudioPromptHistoryOpen(true);
    setAudioPromptStatus("");
    setIsAudioPromptLoading(true);
    try {
      setAudioPromptItems(await listAudioPrompts(30));
    } catch (caught) {
      setAudioPromptStatus(caught instanceof Error ? caught.message : "음성 프롬프트 내역 조회 실패");
    } finally {
      setIsAudioPromptLoading(false);
    }
  }

  async function deleteAudioPromptHistoryItem(promptId: string) {
    setAudioPromptStatus("");
    try {
      await deleteAudioPrompt(promptId);
      setAudioPromptItems((current) => current.filter((item) => item.id !== promptId));
      setAudioPromptStatus("음성 프롬프트 삭제 완료");
    } catch (caught) {
      setAudioPromptStatus(caught instanceof Error ? caught.message : "음성 프롬프트 삭제 실패");
    }
  }

  async function toggleReviewAudioEnabled() {
    const nextAudioEnabled = !audioEnabled;
    const previousAudioEnabled = audioEnabled;
    setAudioEnabled(nextAudioEnabled);
    setError("");
    try {
      if (groupId) {
        await updateMarketingAudioSettings(groupId, {
          audioEnabled: nextAudioEnabled,
          audioPrompt,
        });
      }
      setStatus(
        nextAudioEnabled
          ? "음성 유지가 켜졌습니다. 기존 생성 영상에 오디오가 없다면 필요한 컷을 재생성하세요."
          : "음성 유지가 꺼졌습니다. 최종 합치기에서 오디오를 제거합니다.",
      );
    } catch (caught) {
      setAudioEnabled(previousAudioEnabled);
      setError(caught instanceof Error ? caught.message : "음성 설정 저장 실패");
    }
  }

  async function loadClipPromptHistory() {
    setClipPromptStatus("");
    setIsClipPromptLoading(true);
    try {
      const [savedPrompts, groups] = await Promise.all([
        listClipPrompts(50),
        listMarketingReelGroups(20),
      ]);
      setClipPromptItems(savedPrompts);
      const detailResults = await Promise.allSettled(groups.map((group) => getMarketingReelGroup(group.group_id)));
      const groupById = new Map(groups.map((group) => [group.group_id, group]));
      const nextHistoryPrompts = detailResults.flatMap((result) => {
        if (result.status !== "fulfilled") return [];
        const detail = result.value;
        const group = groupById.get(detail.group_id);
        const groupTitle = group ? historyDisplayTitle(group) : detail.final_title?.trim() || `${detail.clips.length} clips`;
        return detail.clips
          .filter((clip) => clip.initial_prompt.trim())
          .map((clip): ClipPromptSourceItem => ({
            id: `history-${detail.group_id}-${clip.clip_id}`,
            title: `${groupTitle} · Clip ${clip.current_order}`,
            prompt: clip.initial_prompt,
            created_at: detail.created_at,
            source: "history",
            sourceLabel: `${statusLabel(detail.status)} · ${formatHistoryDate(detail.created_at)}`,
            groupId: detail.group_id,
            clipId: clip.clip_id,
          }));
      });
      setHistoryClipPromptItems(nextHistoryPrompts);
    } catch (caught) {
      setClipPromptStatus(caught instanceof Error ? caught.message : "Clip prompt 내역 조회 실패");
    } finally {
      setIsClipPromptLoading(false);
    }
  }

  function openClipPromptSave(clip: ClipDraft, index: number) {
    setClipPromptTargetId(clip.clientImageId);
    setClipPromptMode("save");
    setClipPromptStatus("");
    setClipPromptQuery("");
    setClipPromptTitle(`${index + 1}. ${clip.file.name}`);
  }

  function openClipPromptHistory(clip: ClipDraft) {
    setClipPromptTargetId(clip.clientImageId);
    setClipPromptMode("load");
    setClipPromptTab("saved");
    setClipPromptStatus("");
    setClipPromptQuery("");
    void loadClipPromptHistory();
  }

  function closeClipPromptModal() {
    setClipPromptMode(null);
    setClipPromptTargetId("");
    setClipPromptStatus("");
    setClipPromptTitle("");
    setClipPromptQuery("");
  }

  async function saveSelectedClipPrompt() {
    if (!selectedClipPromptTarget) return;
    if (!clipPromptTitle.trim()) {
      setClipPromptStatus("제목을 입력하세요.");
      return;
    }
    if (!selectedClipPromptTarget.prompt.trim()) {
      setClipPromptStatus("저장할 prompt를 입력하세요.");
      return;
    }
    setIsClipPromptSaving(true);
    setClipPromptStatus("");
    try {
      const saved = await saveClipPrompt(clipPromptTitle, selectedClipPromptTarget.prompt);
      setClipPromptItems((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      closeClipPromptModal();
      showHistoryToast("Clip prompt를 저장했습니다.");
    } catch (caught) {
      setClipPromptStatus(caught instanceof Error ? caught.message : "Clip prompt 저장 실패");
    } finally {
      setIsClipPromptSaving(false);
    }
  }

  function applyClipPrompt(item: ClipPromptSourceItem) {
    if (!clipPromptTargetId) return;
    updateClip(clipPromptTargetId, { prompt: item.prompt });
    closeClipPromptModal();
    showHistoryToast("Clip prompt를 적용했습니다.");
  }

  async function deleteClipPromptHistoryItem(promptId: string) {
    setClipPromptStatus("");
    try {
      await deleteClipPrompt(promptId);
      setClipPromptItems((current) => current.filter((item) => item.id !== promptId));
      setClipPromptStatus("Clip prompt 삭제 완료");
    } catch (caught) {
      setClipPromptStatus(caught instanceof Error ? caught.message : "Clip prompt 삭제 실패");
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
    setSelectedReferenceId(group.group_id);
    setHistoryStatus("");
    try {
      const detail = await getMarketingReelGroup(group.group_id);
      setSelectedReference(detail);
      setHistoryTitleDraft(detail.final_title || "");
      setSelectedReferenceClipId("");
    } catch (caught) {
      setHistoryStatus(caught instanceof Error ? caught.message : "히스토리 상세 조회 실패");
    } finally {
      setSelectedReferenceId("");
    }
  }

  function clipsFromReference(detail: MarketingReelGroupDetail, includeAttempts: boolean): ClipDraft[] {
    return detail.clips.map((clip) => {
      const attempts = includeAttempts ? clip.attempts.map(attemptFromDetail) : [];
      const selectedAttempt = attempts.find((attempt) => attempt.attemptId === clip.approved_attempt_id) ?? attempts.at(-1);
      return {
        clientImageId: clip.client_image_id,
        file: fileFromRemoteUrl(clip.source_image_url, `clip-${clip.current_order}.png`),
        previewUrl: clip.source_image_url,
        clipId: includeAttempts ? clip.clip_id : undefined,
        uploadedUrl: clip.source_image_url,
        sourceImageUrl: clip.source_image_url,
        sourceGenerationUrl: clip.source_image_url,
        endUploadedUrl: clip.end_image_url ?? undefined,
        endImageUrl: clip.end_image_url ?? undefined,
        endGenerationUrl: clip.end_image_url ?? undefined,
        endPreviewUrl: clip.end_image_url ?? undefined,
        generationMode: clip.generation_mode,
        order: clip.current_order,
        prompt: clip.initial_prompt,
        targetDurationSec: normalizeSourceDurationSec(clip.target_duration_sec),
        attempts,
        approvedAttemptId: includeAttempts ? clip.approved_attempt_id ?? selectedAttempt?.attemptId : undefined,
        viewingAttemptId: selectedAttempt?.attemptId,
      };
    });
  }

  function importReferenceToStep1() {
    if (!selectedReference) return;
    clearMarketingQuery();
    const restoredAspectRatio = normalizeGenerationAspectRatio(selectedReference.aspect_ratio);
    const restoredVideoQuality = normalizeMarketingVideoQuality(selectedReference.video_quality);
    setGlobalPrompt(selectedReference.global_prompt);
    setAudioEnabled(Boolean(selectedReference.audio_enabled));
    setAudioPrompt(selectedReference.audio_prompt || initialAudioPrompt);
    setAspectRatio(restoredAspectRatio);
    setGenerationAspectRatio(restoredAspectRatio);
    setVideoQuality(restoredVideoQuality);
    setGroupId("");
    setFinalUrl("");
    setFinalSaveError("");
    setLastFinalPersistPayload(null);
    setProgress(0);
    setStatus("선택한 히스토리의 Step 1 설정을 가져왔습니다.");
    setClips(clipsFromReference(selectedReference, false));
    setActiveStep(1);
    setHistoryOpen(false);
    showHistoryToast("Step 1 설정을 복원했습니다.");
  }

  function restoreReferenceToStep2(
    detail: MarketingReelGroupDetail,
    options: {
      closeHistory?: boolean;
      statusMessage?: string;
      toastMessage?: string;
      urlMode?: "push" | "replace";
    } = {},
  ) {
    const restoredAspectRatio = normalizeGenerationAspectRatio(detail.aspect_ratio);
    const restoredVideoQuality = normalizeMarketingVideoQuality(detail.video_quality);
    setSelectedReference(detail);
    setHistoryTitleDraft(detail.final_title || "");
    setSelectedReferenceClipId("");
    setGlobalPrompt(detail.global_prompt);
    setAudioEnabled(Boolean(detail.audio_enabled));
    setAudioPrompt(detail.audio_prompt || initialAudioPrompt);
    setAspectRatio(restoredAspectRatio);
    setGenerationAspectRatio(restoredAspectRatio);
    setVideoQuality(restoredVideoQuality);
    setGroupId(detail.group_id);
    setFinalUrl(detail.final_video_url ?? "");
    setProgress(100);
    setStatus(options.statusMessage ?? "선택한 히스토리의 Step 2 결과를 불러왔습니다.");
    setClips(clipsFromReference(detail, true));
    setActiveStep(2);
    setFinalSaveError("");
    setLastFinalPersistPayload(null);
    if (options.closeHistory ?? true) setHistoryOpen(false);
    if (options.urlMode) writeStep2Query(detail.group_id, options.urlMode);
    if (options.toastMessage) showHistoryToast(options.toastMessage);
  }

  function loadReferenceToStep2() {
    if (!selectedReference) return;
    restoreReferenceToStep2(selectedReference, {
      toastMessage: "Step 2 결과를 열었습니다.",
      urlMode: "push",
    });
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
        <div className="marketing-tool-actions">
          {hasWorkspaceState ? (
            <button className="history-toggle secondary" type="button" disabled={isBusy} onClick={resetMarketingWorkspace}>
              새 작업 시작
            </button>
          ) : null}
          {hasStartedGeneration ? (
            <button className="history-toggle secondary" type="button" disabled={isBusy} onClick={restoreCurrentDataToStep1Draft}>
              이 데이터로 작업 시작
            </button>
          ) : null}
          <button className="history-toggle" type="button" onClick={openHistoryModal}>
            히스토리 열기
          </button>
        </div>
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
              onClick={() => selectWorkflowStep(step)}
            >
              <span>{step}</span>{label}
            </button>
          );
        })}
      </nav>

      {error ? <p className="marketing-alert" role="alert">{error}</p> : null}
      {isRestoringFromUrl ? <p className="status-line" role="status">저장된 Step 2를 불러오는 중...</p> : null}

      <section className="marketing-workflow-grid">
        <div className="marketing-workspace">
          {activeStep === 1 ? (
            <section className="marketing-panel workflow-panel">
              <header className="workflow-panel-header">
                <div>
                  <h2>1. 생성 전</h2>
                  <p>공간 사진을 올리고 이미지별 prompt와 영상 길이를 설정합니다.</p>
                </div>
                <button className="run-reel-btn" type="button" disabled={isBusy || isSequenceLocked || activeClips.length < minimumImageCount} onClick={startSourceGeneration}>
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
                  <span className="brief-label">Video ratio</span>
                  <select className="brief-select" value={aspectRatio} disabled={isSequenceLocked} onChange={(event) => setAspectRatio(normalizeMarketingAspectRatio(event.target.value))}>
                    {allowedMarketingAspectRatioOptions.map((ratio) => <option key={ratio} value={ratio}>{aspectRatioLabels[ratio]}</option>)}
                  </select>
                </label>
                <label className="brief-field">
                  <span className="brief-label">Video quality</span>
                  <select className="brief-select" value={videoQuality} disabled={isSequenceLocked} onChange={(event) => setVideoQuality(normalizeMarketingVideoQuality(event.target.value))}>
                    {allowedMarketingVideoQualities.map((quality) => <option key={quality} value={quality}>{videoQualityLabels[quality]}</option>)}
                  </select>
                </label>
                <label className="brief-field is-wide">
                  <span className="brief-label">Global prompt</span>
                  <textarea className="brief-textarea" value={globalPrompt} disabled={isSequenceLocked} onChange={(event) => setGlobalPrompt(event.target.value)} />
                </label>
                <div className="prompt-actions">
                  <button type="button" disabled={isPromptSaving || isSequenceLocked} onClick={saveCurrentGlobalPrompt}>
                    {isPromptSaving ? "저장 중..." : "Global prompt 저장"}
                  </button>
                  <button type="button" disabled={isSequenceLocked} onClick={openGlobalPromptHistory}>
                    Global prompt 가져오기
                  </button>
                </div>
                {promptHistoryStatus ? <p className="prompt-history-status" role="status">{promptHistoryStatus}</p> : null}
                <div className="audio-prompt-toggle-row">
                  <button
                    className={`audio-toggle ${audioEnabled ? "is-on" : ""}`}
                    type="button"
                    role="switch"
                    aria-checked={audioEnabled}
                    aria-label="음성 생성"
                    disabled={isSequenceLocked}
                    onClick={() => setAudioEnabled((current) => !current)}
                  >
                    음성 {audioEnabled ? "ON" : "OFF"}
                  </button>
                  <span>ON이면 Kling 요청에 sound=on을 전달하고 음성 프롬프트를 최종 prompt에 함께 보냅니다.</span>
                </div>
                <label className="brief-field is-wide">
                  <span className="brief-label">음성 프롬프트</span>
                  <textarea
                    className="brief-textarea"
                    value={audioPrompt}
                    disabled={isSequenceLocked || !audioEnabled}
                    placeholder={initialAudioPrompt}
                    onChange={(event) => setAudioPrompt(event.target.value)}
                  />
                </label>
                <div className="prompt-actions">
                  <button type="button" disabled={isAudioPromptSaving || isSequenceLocked || !audioEnabled} onClick={saveCurrentAudioPrompt}>
                    {isAudioPromptSaving ? "저장 중..." : "음성 프롬프트 저장"}
                  </button>
                  <button type="button" disabled={isSequenceLocked} onClick={openAudioPromptHistory}>
                    음성 프롬프트 가져오기
                  </button>
                </div>
                {audioPromptStatus ? <p className="prompt-history-status" role="status">{audioPromptStatus}</p> : null}
                <label className="brief-field is-wide upload-field">
                  <span className="brief-label">Images</span>
                  <span className="upload-count">{activeClips.length} / 10 selected</span>
                  <input aria-label="이미지 선택" type="file" accept="image/*" multiple disabled={isSequenceLocked} onChange={(event) => handleFiles(event.currentTarget.files)} />
                </label>
              </div>

              <div className="image-editor-list">
                {isRestoringFromUrl ? (
                  <div className="empty-state">저장된 Step 2를 불러오는 중...</div>
                ) : activeClips.length === 0 ? (
                  <div className="empty-state">아직 선택된 사진이 없습니다.</div>
                ) : activeClips.map((clip, index) => {
                  const startFrameRatioClass = frameRatioClass(clip.sourcePreviewAspectRatio);
                  const endPreviewUrl = resolveEndPreviewUrl(clip, index);
                  const endFrameRatioClass = frameRatioClass(resolveEndPreviewAspectRatio(clip, index) ?? clip.sourcePreviewAspectRatio);
                  return (
                    <article className="image-editor-card" key={clip.clientImageId}>
                      <div className="frame-pair-editor">
                        <figure>
                          <span>Start Frame</span>
                          <button
                            className="frame-preview-trigger"
                            type="button"
                            aria-label={`Clip ${index + 1} Start Frame 확대`}
                            onClick={() => setActiveFramePreviewId(`${clip.clientImageId}:start`)}
                          >
                            <img className={startFrameRatioClass} src={clip.previewUrl} alt={`${index + 1}번 Start Frame 미리보기`} />
                          </button>
                        </figure>
                        <figure>
                          <span>End Frame</span>
                          {endPreviewUrl ? (
                            <button
                              className="frame-preview-trigger"
                              type="button"
                              aria-label={`Clip ${index + 1} End Frame 확대`}
                              onClick={() => setActiveFramePreviewId(`${clip.clientImageId}:end`)}
                            >
                              <img className={endFrameRatioClass} src={endPreviewUrl} alt={`${index + 1}번 End Frame 미리보기`} />
                            </button>
                          ) : (
                            <div className={`frame-empty ${endFrameRatioClass}`}>Optional</div>
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
                        <span>{generationModeLabel(clip.generationMode)} · {aspectRatioLabels[displayAspectRatio]} · {videoQualityLabels[videoQuality]} · 영상 {clip.targetDurationSec}초</span>
                      </header>
                      <textarea
                        className="brief-textarea"
                        placeholder="이 이미지로 만들고 싶은 장면을 입력하세요."
                        value={clip.prompt}
                        disabled={isSequenceLocked}
                        onChange={(event) => updateClip(clip.clientImageId, { prompt: event.target.value })}
                      />
                      <label className="clip-duration-setting">
                        <span className="clip-duration-copy">
                          <span className="brief-label">영상 길이</span>
                          <small>이 이미지로 생성할 비디오 길이를 선택합니다.</small>
                        </span>
                        <select
                          className="brief-select clip-duration-select"
                          aria-label={`${index + 1}번 이미지 영상 길이`}
                          value={clip.targetDurationSec}
                          disabled={isSequenceLocked}
                          onChange={(event) => updateClip(clip.clientImageId, { targetDurationSec: normalizeSourceDurationSec(event.target.value) })}
                        >
                          {allowedSourceDurationsSec.map((duration) => <option key={duration} value={duration}>{duration}초</option>)}
                        </select>
                      </label>
                      <div className="clip-controls">
                        <button className="icon-control-button" type="button" aria-label={`${index + 1}번 이미지 위로 이동`} title="위로 이동" disabled={isSequenceLocked || index === 0} onClick={() => setClipOrder(moveImage(activeClips, index, -1))}>
                          <span className="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
                        </button>
                        <button className="icon-control-button" type="button" aria-label={`${index + 1}번 이미지 아래로 이동`} title="아래로 이동" disabled={isSequenceLocked || index === activeClips.length - 1} onClick={() => setClipOrder(moveImage(activeClips, index, 1))}>
                          <span className="material-symbols-outlined" aria-hidden="true">arrow_downward</span>
                        </button>
                        <button type="button" disabled={isSequenceLocked} onClick={() => setClipOrder(removeImage(activeClips, index))}>삭제</button>
                        <button type="button" disabled={isSequenceLocked || !clip.prompt.trim()} onClick={() => openClipPromptSave(clip, index)}>Prompt 저장</button>
                        <button type="button" disabled={isSequenceLocked} onClick={() => openClipPromptHistory(clip)}>Prompt 가져오기</button>
                      </div>
                      </div>
                    </article>
                  );
                })}
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
                <div className="step-audio-actions">
                  <button
                    className={`audio-toggle ${audioEnabled ? "is-on" : ""}`}
                    type="button"
                    role="switch"
                    aria-checked={audioEnabled}
                    aria-label="최종 합치기 음성 유지"
                    disabled={isBusy}
                    onClick={toggleReviewAudioEnabled}
                  >
                    음성 {audioEnabled ? "ON" : "OFF"}
                  </button>
                  <button className="run-reel-btn" type="button" disabled={!canCompile || isBusy} onClick={() => setActiveStep(3)}>
                    승인본으로 합치기 준비
                  </button>
                </div>
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
                  const endPreviewUrl = resolveEndPreviewUrl(clip, index);
                  const mediaRatioClass = `ratio-${displayAspectRatio.replace(":", "-")}`;
                  return (
                    <article className="clip-review-card" key={clip.clientImageId}>
                      <div className="clip-review-media-row">
                        <figure className="clip-review-media-slot">
                          <span>Start Frame</span>
                          <button
                            className="frame-preview-trigger"
                            type="button"
                            aria-label={`Clip ${index + 1} Start Frame 확대`}
                            onClick={() => setActiveFramePreviewId(`${clip.clientImageId}:start`)}
                          >
                            <img className={mediaRatioClass} src={clip.previewUrl} alt={`${index + 1}번 Start Frame`} />
                          </button>
                        </figure>
                        <figure className={`clip-review-media-slot${endPreviewUrl ? "" : " frame-placeholder"}`}>
                          <span>{clip.generationMode === "NEXT_START_AS_END" ? "End Frame (Next Start)" : "End Frame"}</span>
                          {endPreviewUrl ? (
                            <button
                              className="frame-preview-trigger"
                              type="button"
                              aria-label={`Clip ${index + 1} End Frame 확대`}
                              onClick={() => setActiveFramePreviewId(`${clip.clientImageId}:end`)}
                            >
                              <img className={mediaRatioClass} src={endPreviewUrl} alt={`${index + 1}번 End Frame`} />
                            </button>
                          ) : (
                            <div className={`frame-empty ${mediaRatioClass}`}>End Frame 없음</div>
                          )}
                        </figure>
                        <figure className="clip-review-media-slot">
                          <span>Video</span>
                          {selectedAttempt?.videoUrl ? (
                            <video className={mediaRatioClass} src={selectedAttempt.videoUrl} controls playsInline />
                          ) : (
                            <div className={`video-placeholder ${mediaRatioClass}`}>{selectedAttempt?.status ?? "대기"}</div>
                          )}
                        </figure>
                      </div>
                      <div className="clip-review-info-row">
                        <header>
                          <h3>Clip {index + 1}</h3>
                          <span className={`status-badge status-${(selectedAttempt?.status ?? "QUEUED").toLowerCase()}`}>
                            {clip.approvedAttemptId ? "승인됨" : selectedAttempt?.status ?? "대기"}
                          </span>
                        </header>
                        <div className="clip-review-meta" aria-label={`Clip ${index + 1} 생성 설정`}>
                          <span>{generationModeLabel(clip.generationMode)}</span>
                          <span>{aspectRatioLabels[displayAspectRatio]}</span>
                          <span>{videoQualityLabels[videoQuality]}</span>
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
                          <button type="button" onClick={() => { setRegeneratingClipId(clip.clientImageId); setRegeneratePrompt(selectedAttempt?.prompt ?? clip.prompt); }}>재생성 설정</button>
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
                <button className="run-reel-btn" type="button" disabled={!canCompile || isBusy} onClick={compileFinal}>
                  최종 영상 합치기
                </button>
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
                  <video className={`final-video ratio-${displayAspectRatio.replace(":", "-")}`} src={finalUrl} controls playsInline />
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
          <div className="history-modal-backdrop" role="presentation">
            <aside className="marketing-panel history-panel" role="dialog" aria-modal="true" aria-label="공용 히스토리">
            <header className="history-panel-header">
              <div>
                <h3>공용 히스토리</h3>
                <p>
                  {historyLastLoadedAt
                    ? `${historyItems.length}개 저장됨 · 마지막 갱신 ${formatHistoryRefreshTime(historyLastLoadedAt)}`
                    : "완성본과 Step 2 결과를 다시 여는 공간입니다."}
                </p>
              </div>
              <div className="history-modal-actions">
                <button type="button" disabled={isHistoryLoading} onClick={loadHistory}>
                  {isHistoryLoading ? "불러오는 중" : "히스토리 새로고침"}
                </button>
                <button type="button" onClick={() => setHistoryOpen(false)}>닫기</button>
              </div>
            </header>
            <div className="history-toolbar">
              <label className="history-search">
                <span className="material-symbols-outlined" aria-hidden="true">search</span>
                <input
                  value={historyQuery}
                  placeholder="제목, 상태, 날짜 검색"
                  onChange={(event) => setHistoryQuery(event.target.value)}
                />
              </label>
              <div className="history-filter-tabs" role="group" aria-label="히스토리 필터">
                <button className={historyFilter === "all" ? "is-active" : ""} type="button" onClick={() => setHistoryFilter("all")}>
                  전체 {historyItems.length}
                </button>
                <button className={historyFilter === "final" ? "is-active" : ""} type="button" onClick={() => setHistoryFilter("final")}>
                  완성본 {finalHistoryCount}
                </button>
                <button className={historyFilter === "review" ? "is-active" : ""} type="button" onClick={() => setHistoryFilter("review")}>
                  작업중 {reviewHistoryCount}
                </button>
              </div>
            </div>
            {historyStatus ? <p className="history-panel-status" role="status">{historyStatus}</p> : null}
            <div className="history-list">
              {isHistoryLoading ? <p className="status-line">히스토리를 불러오는 중...</p> : null}
              {!isHistoryLoading && historyItems.length === 0 ? (
                <div className="history-empty-state">
                  <b>아직 불러온 히스토리가 없습니다.</b>
                  <span>새로고침으로 저장된 릴스를 가져온 뒤 Step 1 설정이나 Step 2 결과를 다시 열 수 있습니다.</span>
                </div>
              ) : null}
              {!isHistoryLoading && historyItems.length > 0 && filteredHistoryItems.length === 0 ? (
                <div className="history-empty-state">
                  <b>조건에 맞는 히스토리가 없습니다.</b>
                  <span>검색어를 지우거나 필터를 전체로 바꿔보세요.</span>
                </div>
              ) : null}
              {filteredHistoryItems.map((item) => (
                <button
                  className={selectedReference?.group_id === item.group_id ? "is-selected" : ""}
                  key={item.group_id}
                  type="button"
                  aria-busy={selectedReferenceId === item.group_id}
                  onClick={() => selectReference(item)}
                >
                  <span className={`history-status-pill status-${item.status.toLowerCase()}`}>{statusLabel(item.status)}</span>
                  {item.representative_image_url ? (
                    <img src={item.representative_image_url} alt={`${historyDisplayTitle(item)} 대표 이미지`} loading="lazy" />
                  ) : (
                    <span className="history-thumb-placeholder" aria-hidden="true">MP4</span>
                  )}
                  <b>{historyDisplayTitle(item)}</b>
                  <small>{item.clip_count} clips · {formatHistoryDate(item.created_at)}</small>
                  <small>{item.final_video_url ? "최종 영상 저장됨" : "Step 2 작업 상태"}</small>
                </button>
              ))}
            </div>
            {selectedReference ? (
              <section className="history-detail">
                <div className="history-detail-heading">
                  <div>
                    <span className={`history-status-pill status-${selectedReference.status.toLowerCase()}`}>{statusLabel(selectedReference.status)}</span>
                    <h4>히스토리 상세</h4>
                    <p>{selectedReference.clips.length} clips · {formatHistoryDate(selectedReference.created_at)}</p>
                  </div>
                </div>
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
                <div className="history-import-actions">
                  <button type="button" onClick={importReferenceToStep1}>
                    <span className="material-symbols-outlined" aria-hidden="true">edit_note</span>
                    Step 1 설정으로 복원
                  </button>
                  <button type="button" onClick={loadReferenceToStep2}>
                    <span className="material-symbols-outlined" aria-hidden="true">video_library</span>
                    Step 2 결과 열기
                  </button>
                </div>
                <div className="history-detail-summary">
                  <span>Global prompt</span>
                  <p>{selectedReference.global_prompt || "저장된 Global prompt가 없습니다."}</p>
                </div>
                <div className="history-detail-summary">
                  <span>Audio</span>
                  <p>{selectedReference.audio_enabled ? selectedReference.audio_prompt || "음성 생성 ON" : "음성 생성 OFF"}</p>
                </div>
                {selectedReference.final_video_url ? (
                  <div className="history-final-preview">
                    <span>Final render</span>
                    <video src={selectedReference.final_video_url} controls playsInline />
                  </div>
                ) : null}
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
            </aside>
          </div>
        ) : null}
        {historyToast ? (
          <div className="marketing-toast" role="status" aria-live="polite">
            <span className="material-symbols-outlined" aria-hidden="true">check_circle</span>
            {historyToast}
          </div>
        ) : null}
      </section>
      {activeFramePreview ? (
        <div className="frame-preview-modal-backdrop" role="presentation">
          <section className="frame-preview-modal" role="dialog" aria-modal="true" aria-label="Frame preview">
            <header>
              <div>
                <h2>{`Clip ${activeFramePreview.clipIndex + 1} · ${activeFramePreview.frameType}`}</h2>
                <p>{`${reviewFramePreviews.findIndex((frame) => frame.id === activeFramePreview.id) + 1} / ${reviewFramePreviews.length}`}</p>
              </div>
              <button type="button" aria-label="프레임 미리보기 닫기" onClick={() => setActiveFramePreviewId(null)}>
                닫기
              </button>
            </header>
            <div className="frame-preview-stage">
              <button className="frame-preview-nav" type="button" aria-label="이전 프레임" onClick={() => navigateFramePreview(-1)}>
                <span className="material-symbols-outlined" aria-hidden="true">chevron_left</span>
              </button>
              <img
                src={activeFramePreview.url}
                alt={`Clip ${activeFramePreview.clipIndex + 1} ${activeFramePreview.frameType} 확대`}
              />
              <button className="frame-preview-nav" type="button" aria-label="다음 프레임" onClick={() => navigateFramePreview(1)}>
                <span className="material-symbols-outlined" aria-hidden="true">chevron_right</span>
              </button>
            </div>
          </section>
        </div>
      ) : null}
      {promptHistoryOpen ? (
        <div className="prompt-modal-backdrop" role="presentation">
          <section className="prompt-modal" role="dialog" aria-modal="true" aria-label="Global prompt 내역">
            <header>
              <div>
                <h2>Global prompt 내역</h2>
                <p>저장된 문장을 선택하면 현재 입력창에 바로 적용됩니다.</p>
              </div>
              <button type="button" onClick={() => setPromptHistoryOpen(false)}>닫기</button>
            </header>
            {isPromptHistoryLoading ? <p className="status-line">불러오는 중...</p> : null}
            {!isPromptHistoryLoading && promptHistoryItems.length === 0 ? (
              <p className="status-line">저장된 Global prompt가 없습니다.</p>
            ) : null}
            <div className="prompt-history-list">
              {promptHistoryItems.map((item) => (
                <article className="prompt-history-item" key={item.id}>
                  <button
                    type="button"
                    aria-label={`적용: ${item.global_prompt}`}
                    onClick={() => {
                      setGlobalPrompt(item.global_prompt);
                      setPromptHistoryOpen(false);
                    }}
                  >
                    <span>{item.global_prompt}</span>
                    <small>{new Date(item.created_at).toLocaleString()}</small>
                  </button>
                  <button className="prompt-delete-button" type="button" onClick={() => deletePromptHistoryItem(item.id)}>
                    삭제
                  </button>
                </article>
              ))}
            </div>
          </section>
        </div>
      ) : null}
      {audioPromptHistoryOpen ? (
        <div className="prompt-modal-backdrop" role="presentation">
          <section className="prompt-modal" role="dialog" aria-modal="true" aria-label="음성 프롬프트 내역">
            <header>
              <div>
                <h2>음성 프롬프트 내역</h2>
                <p>저장된 음성 지시문을 선택하면 음성 토글이 켜지고 현재 입력창에 적용됩니다.</p>
              </div>
              <button type="button" onClick={() => setAudioPromptHistoryOpen(false)}>닫기</button>
            </header>
            {isAudioPromptLoading ? <p className="status-line">불러오는 중...</p> : null}
            {!isAudioPromptLoading && audioPromptItems.length === 0 ? (
              <p className="status-line">저장된 음성 프롬프트가 없습니다.</p>
            ) : null}
            <div className="prompt-history-list">
              {audioPromptItems.map((item) => (
                <article className="prompt-history-item" key={item.id}>
                  <button
                    type="button"
                    aria-label={`적용: ${item.prompt}`}
                    onClick={() => {
                      setAudioPrompt(item.prompt);
                      setAudioEnabled(true);
                      setAudioPromptHistoryOpen(false);
                    }}
                  >
                    <b>{item.title}</b>
                    <span>{item.prompt}</span>
                    <small>{new Date(item.created_at).toLocaleString()}</small>
                  </button>
                  <button className="prompt-delete-button" type="button" onClick={() => deleteAudioPromptHistoryItem(item.id)}>
                    삭제
                  </button>
                </article>
              ))}
            </div>
          </section>
        </div>
      ) : null}
      {clipPromptMode ? (
        <div className="prompt-modal-backdrop" role="presentation">
          <section className="prompt-modal clip-prompt-modal" role="dialog" aria-modal="true" aria-label="Clip prompt 내역">
            <header>
              <div>
                <h2>{clipPromptMode === "save" ? "Clip prompt 저장" : "Clip prompt 가져오기"}</h2>
                <p>{selectedClipPromptTarget ? selectedClipPromptTarget.file.name : "선택한 row"}에 적용할 Step 1 row prompt 히스토리입니다.</p>
              </div>
              <button type="button" onClick={closeClipPromptModal}>닫기</button>
            </header>
            <div className="clip-prompt-body">
              {clipPromptMode === "save" ? (
                <div className="clip-prompt-save-form">
                  <label className="brief-field">
                    <span className="brief-label">Title</span>
                    <input
                      className="brief-input"
                      value={clipPromptTitle}
                      placeholder="예: 커튼 클로즈업 오프닝"
                      onChange={(event) => setClipPromptTitle(event.target.value)}
                    />
                  </label>
                  <div className="history-detail-summary">
                    <span>Prompt</span>
                    <p>{selectedClipPromptTarget?.prompt || "저장할 prompt가 없습니다."}</p>
                  </div>
                  <button className="run-reel-btn" type="button" disabled={isClipPromptSaving} onClick={saveSelectedClipPrompt}>
                    {isClipPromptSaving ? "저장 중..." : "저장"}
                  </button>
                </div>
              ) : (
                <>
                  <div className="clip-prompt-tabs" role="tablist" aria-label="Clip prompt 출처">
                    <button
                      className={clipPromptTab === "saved" ? "is-active" : ""}
                      type="button"
                      role="tab"
                      aria-selected={clipPromptTab === "saved"}
                      onClick={() => setClipPromptTab("saved")}
                    >
                      저장한 Prompt {clipPromptItems.length}
                    </button>
                    <button
                      className={clipPromptTab === "history" ? "is-active" : ""}
                      type="button"
                      role="tab"
                      aria-selected={clipPromptTab === "history"}
                      onClick={() => setClipPromptTab("history")}
                    >
                      히스토리 Prompt {historyClipPromptItems.length}
                    </button>
                  </div>
                  <label className="history-search clip-prompt-search">
                    <span className="material-symbols-outlined" aria-hidden="true">search</span>
                    <input
                      value={clipPromptQuery}
                      placeholder="제목, 출처 또는 prompt 검색"
                      onChange={(event) => setClipPromptQuery(event.target.value)}
                    />
                  </label>
                  {isClipPromptLoading ? <p className="status-line">불러오는 중...</p> : null}
                  {!isClipPromptLoading && visibleClipPromptItems.length === 0 ? (
                    <p className="status-line">
                      {clipPromptTab === "saved" ? "저장된 Clip prompt가 없습니다." : "가져올 히스토리 prompt가 없습니다."}
                    </p>
                  ) : null}
                  {!isClipPromptLoading && visibleClipPromptItems.length > 0 && filteredClipPromptItems.length === 0 ? (
                    <p className="status-line">검색 결과가 없습니다.</p>
                  ) : null}
                  <div className="prompt-history-list">
                    {filteredClipPromptItems.map((item) => (
                      <article className="prompt-history-item" key={item.id}>
                        <button
                          type="button"
                          aria-label={`적용: ${item.title}`}
                          onClick={() => applyClipPrompt(item)}
                        >
                          <b>{item.title}</b>
                          <small>{item.sourceLabel}</small>
                          <span>{item.prompt}</span>
                          <small>{new Date(item.created_at).toLocaleString()}</small>
                        </button>
                        {item.source === "saved" ? (
                          <button className="prompt-delete-button" type="button" onClick={() => deleteClipPromptHistoryItem(item.id)}>
                            삭제
                          </button>
                        ) : null}
                      </article>
                    ))}
                  </div>
                </>
              )}
            </div>
            {clipPromptStatus ? <p className="prompt-history-status" role="status">{clipPromptStatus}</p> : null}
          </section>
        </div>
      ) : null}
    </main>
  );
}
