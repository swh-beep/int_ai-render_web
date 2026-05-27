import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const marketingApi = vi.hoisted(() => ({
  approveMarketingClipAttempt: vi.fn(),
  createMarketingClipGeneration: vi.fn(),
  createMarketingClipAttempt: vi.fn(),
  createMarketingReelGroup: vi.fn(),
  deleteAudioPrompt: vi.fn(),
  deleteClipPrompt: vi.fn(),
  deleteGlobalPrompt: vi.fn(),
  deleteMarketingReelClip: vi.fn(),
  getMarketingReelGroup: vi.fn(),
  listAudioPrompts: vi.fn(),
  listClipPrompts: vi.fn(),
  listGlobalPrompts: vi.fn(),
  listMarketingReelGroups: vi.fn(),
  markMarketingReelGroupFailed: vi.fn(),
  patchMarketingFinalResult: vi.fn(),
  saveAudioPrompt: vi.fn(),
  saveClipPrompt: vi.fn(),
  saveGlobalPrompt: vi.fn(),
  updateMarketingAudioSettings: vi.fn(),
  updateMarketingReelGroupTitle: vi.fn(),
  updateMarketingClipSourceImages: vi.fn(),
  updateMarketingClipAttempt: vi.fn(),
}));

const outputsApi = vi.hoisted(() => ({
  publishOutputAsset: vi.fn(),
  uploadOutputImageAssets: vi.fn(),
}));

const videoApi = vi.hoisted(() => ({
  downloadUrlForResult: vi.fn((url: string) => `/download?url=${encodeURIComponent(url)}`),
  fetchVideoJobStatus: vi.fn(),
  requestCompile: vi.fn(),
  requestMarketingCompile: vi.fn(),
  requestSourceGeneration: vi.fn(),
}));

vi.mock("../api/marketingReels", () => marketingApi);
vi.mock("../api/outputs", () => outputsApi);
vi.mock("../api/videoMvp", () => videoApi);

import { MarketingPage } from "./MarketingPage";

function uploadedAssets(urls: string[]) {
  return urls.map((url) => ({ publicUrl: url, readUrl: url }));
}

function step2HistoryDetail(groupId = "history-1") {
  return {
    group_id: groupId,
    status: "REVIEWING",
    created_at: "2026-05-10",
    updated_at: "2026-05-10",
    global_prompt: "loaded generation prompt",
    audio_enabled: true,
    audio_prompt: "loaded audio prompt",
    platform: "",
    tone: "",
    goal: "",
    clips: [1, 2, 3].map((order) => ({
      clip_id: `clip-${order}`,
      client_image_id: `client-${order}`,
      source_image_url: `https://cdn.example/start-${order}.png`,
      generation_mode: "START_ONLY",
      original_order: order,
      current_order: order,
      initial_prompt: `loaded prompt ${order}`,
      target_duration_sec: 5,
      deleted_at: null,
      attempts: [{
        attempt_id: `attempt-${order}`,
        clip_id: `clip-${order}`,
        source_job_id: "job-1",
        source_job_item_index: order - 1,
        prompt: `loaded prompt ${order}`,
        duration_sec: 5,
        status: "COMPLETED",
        source_video_url: `https://cdn.example/clip-${order}.mp4`,
      }],
    })),
  };
}

function openHistory() {
  fireEvent.click(screen.getByRole("button", { name: "히스토리 열기" }));
}

describe("MarketingPage", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/marketing");
    vi.clearAllMocks();
    document.body.style.overflow = "";
    document.body.style.paddingRight = "";
    outputsApi.publishOutputAsset.mockReset();
    outputsApi.uploadOutputImageAssets.mockReset();
    videoApi.fetchVideoJobStatus.mockReset();
    videoApi.requestCompile.mockReset();
    videoApi.requestMarketingCompile.mockReset();
    videoApi.requestSourceGeneration.mockReset();
    videoApi.downloadUrlForResult.mockImplementation((url: string) => `/download?url=${encodeURIComponent(url)}`);
    marketingApi.listMarketingReelGroups.mockReset();
    marketingApi.getMarketingReelGroup.mockReset();
    marketingApi.listAudioPrompts.mockReset();
    marketingApi.listClipPrompts.mockReset();
    marketingApi.updateMarketingAudioSettings.mockReset();
    marketingApi.createMarketingClipGeneration.mockImplementation(async (_groupId, payload) => ({
      group_id: _groupId,
      clip_generation_id: "generation-1",
      generation_type: payload.generation_type,
      status: "RUNNING",
      source_job_id: payload.source_job_id,
      clip_ids: payload.clip_ids,
    }));
    marketingApi.listMarketingReelGroups.mockResolvedValue([]);
    marketingApi.listAudioPrompts.mockResolvedValue([]);
    marketingApi.listClipPrompts.mockResolvedValue([]);
    marketingApi.updateMarketingAudioSettings.mockResolvedValue({ group_id: "group-1" });
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn((file: File) => `blob:${file.name}`),
      revokeObjectURL: vi.fn(),
    });
  });

  it("keeps source generation disabled until at least one image is selected", () => {
    render(<MarketingPage />);

    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeDisabled();
    expect(screen.getByText(/아직 선택된 사진이 없습니다./)).toBeInTheDocument();
  });

  it("presents each Step 1 clip duration as a visible video length setting", () => {
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [new File(["one"], "one.png", { type: "image/png" })],
      },
    });

    expect(screen.getByText("영상 길이")).toBeInTheDocument();
    expect(screen.getByText("이 이미지로 생성할 비디오 길이를 선택합니다.")).toBeInTheDocument();
    expect(screen.getByLabelText("1번 이미지 영상 길이")).toHaveValue("5");
  });

  it("allows a single image to start source generation and enter Step 2", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets(["https://cdn.example/start-1.png"]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4"],
    });
    outputsApi.publishOutputAsset.mockResolvedValueOnce("https://cdn.example/clip-1.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [new File(["one"], "one.png", { type: "image/png" })],
      },
    });
    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    expect(await screen.findByText("Clip 1")).toBeInTheDocument();
    expect(window.location.search).toBe("?path=step2&id=group-1");
    await waitFor(() => expect(marketingApi.createMarketingReelGroup).toHaveBeenCalledWith(expect.objectContaining({
      clips: [expect.objectContaining({ order: 1 })],
    })));
    expect(videoApi.requestSourceGeneration).toHaveBeenCalledWith(expect.objectContaining({
      items: [expect.objectContaining({ url: "https://cdn.example/start-1.png" })],
    }));
    expect(screen.queryByText("Clip 2")).not.toBeInTheDocument();
  });

  it("keeps landscape source ratio across the Step 2 review media row", async () => {
    const createImageBitmapMock = vi.fn(async () => ({
      width: 1600,
      height: 900,
      close: vi.fn(),
    }));
    vi.stubGlobal("createImageBitmap", createImageBitmapMock);
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-landscape",
      aspect_ratio: payload.aspectRatio,
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets(["https://cdn.example/start-landscape.png"]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-landscape",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-landscape");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-landscape.mp4"],
    });
    outputsApi.publishOutputAsset.mockResolvedValueOnce("https://cdn.example/clip-landscape.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);

    const { container } = render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("Video ratio"), { target: { value: "source" } });
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [new File(["landscape"], "landscape.png", { type: "image/png" })],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    expect(await screen.findByText("Clip 1")).toBeInTheDocument();
    expect(marketingApi.createMarketingReelGroup).toHaveBeenCalledWith(expect.objectContaining({ aspectRatio: "16:9" }));
    expect(within(screen.getByLabelText("Clip 1 생성 설정")).getByText("16:9 가로")).toBeInTheDocument();
    const mediaRow = container.querySelector(".clip-review-media-row");
    expect(mediaRow).not.toBeNull();
    expect(mediaRow?.querySelectorAll(".clip-review-media-slot")).toHaveLength(3);
    expect(mediaRow?.querySelector(".clip-review-media-slot.frame-placeholder")).not.toBeNull();
    expect(mediaRow?.querySelectorAll(".ratio-16-9")).toHaveLength(3);
    fireEvent.click(mediaRow?.querySelector(".frame-placeholder .frame-empty") as Element);
    expect(screen.queryByRole("dialog", { name: "Frame preview" })).not.toBeInTheDocument();
  });

  it("shows Step 1 start and end frame previews with the detected image ratio", async () => {
    const createImageBitmapMock = vi.fn()
      .mockResolvedValueOnce({ width: 1600, height: 900, close: vi.fn() })
      .mockResolvedValueOnce({ width: 1600, height: 900, close: vi.fn() });
    vi.stubGlobal("createImageBitmap", createImageBitmapMock);

    const { container } = render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "다음 Start 사용" })[0]);

    const firstEditor = container.querySelector(".image-editor-card");
    await waitFor(() => {
      expect(firstEditor?.querySelectorAll(".frame-preview-trigger .ratio-16-9")).toHaveLength(2);
    });
    expect(firstEditor?.querySelector(".frame-preview-trigger .ratio-9-16")).toBeNull();
  });

  it("keeps the Step 1 empty end frame placeholder aligned to the start frame ratio", async () => {
    const createImageBitmapMock = vi.fn(async () => ({
      width: 1600,
      height: 900,
      close: vi.fn(),
    }));
    vi.stubGlobal("createImageBitmap", createImageBitmapMock);

    const { container } = render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [new File(["one"], "one.png", { type: "image/png" })],
      },
    });

    const firstEditor = container.querySelector(".image-editor-card");
    await waitFor(() => {
      expect(firstEditor?.querySelector(".frame-empty")?.classList.contains("ratio-16-9")).toBe(true);
    });
    expect(firstEditor?.querySelector(".frame-empty")?.classList.contains("ratio-9-16")).toBe(false);
  });

  it("renders the three-step marketing workflow controls", () => {
    render(<MarketingPage />);

    expect(screen.getByRole("button", { name: /1생성 전/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /2비디오 확인/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /3최종 합치기/i })).toBeDisabled();
  });

  it("renders core marketing controls and shared history sections", () => {
    render(<MarketingPage />);

    expect(screen.getByRole("heading", { name: /Marketing Reels Studio/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Content type")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Tone")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Platform")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Goal")).not.toBeInTheDocument();
    expect(within(screen.getByLabelText("Video ratio")).getByRole("option", { name: "이미지 비율대로" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Video quality")).getByRole("option", { name: "720p" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Video quality")).getByRole("option", { name: "1080p" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Global prompt 저장" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Global prompt 가져오기" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "이전 Global prompt 가져오기" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("이미지 선택")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "히스토리 열기" })).toBeInTheDocument();
    openHistory();
    expect(screen.getByRole("dialog", { name: "공용 히스토리" })).toBeInTheDocument();
    expect(screen.getByText("Kling payload preview")).toBeInTheDocument();
    expect(screen.queryByText("Hook")).not.toBeInTheDocument();
    expect(screen.queryByText("Caption")).not.toBeInTheDocument();
    expect(screen.queryByText("CTA")).not.toBeInTheDocument();
  });

  it("enables the audio prompt only when the Step 1 audio toggle is on", () => {
    render(<MarketingPage />);

    const audioSwitch = screen.getByRole("switch", { name: "음성 생성" });
    const audioPrompt = screen.getByLabelText("음성 프롬프트");

    expect(audioSwitch).toHaveAttribute("aria-checked", "false");
    expect(audioPrompt).toBeDisabled();
    expect((audioPrompt as HTMLTextAreaElement).value).toContain("motion-synced audio");

    fireEvent.click(audioSwitch);

    expect(audioSwitch).toHaveAttribute("aria-checked", "true");
    expect(audioPrompt).toBeEnabled();
  });

  it("saves, loads, and deletes audio prompt history from a modal", async () => {
    marketingApi.saveAudioPrompt.mockResolvedValueOnce({
      id: "audio-prompt-1",
      title: "soft room tone",
      prompt: "soft room tone with gentle motion accents",
      created_at: "2026-05-18T00:00:00Z",
    });
    marketingApi.listAudioPrompts.mockResolvedValueOnce([
      {
        id: "audio-prompt-2",
        title: "quiet ambience",
        prompt: "quiet ambience matching the dolly motion",
        created_at: "2026-05-17T00:00:00Z",
      },
    ]);
    marketingApi.deleteAudioPrompt.mockResolvedValueOnce({ id: "audio-prompt-2" });
    render(<MarketingPage />);

    fireEvent.click(screen.getByRole("switch", { name: "음성 생성" }));
    fireEvent.change(screen.getByLabelText("음성 프롬프트"), {
      target: { value: "soft room tone" },
    });
    fireEvent.click(screen.getByRole("button", { name: "음성 프롬프트 저장" }));

    await waitFor(() => expect(marketingApi.saveAudioPrompt).toHaveBeenCalledWith(
      "soft room tone",
      "soft room tone",
    ));
    expect(await screen.findByText("음성 프롬프트 저장 완료")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "음성 프롬프트 가져오기" }));
    expect(await screen.findByRole("dialog", { name: "음성 프롬프트 내역" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    await waitFor(() => expect(marketingApi.deleteAudioPrompt).toHaveBeenCalledWith("audio-prompt-2"));
    expect(screen.queryByText("quiet ambience matching the dolly motion")).not.toBeInTheDocument();
    expect(screen.getByText("음성 프롬프트 삭제 완료")).toBeInTheDocument();

    marketingApi.listAudioPrompts.mockResolvedValueOnce([
      {
        id: "audio-prompt-3",
        title: "quiet ambience",
        prompt: "quiet ambience matching the dolly motion",
        created_at: "2026-05-17T00:00:00Z",
      },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    fireEvent.click(screen.getByRole("button", { name: "음성 프롬프트 가져오기" }));
    fireEvent.click(await screen.findByRole("button", { name: /적용: quiet ambience matching the dolly motion/ }));

    expect(screen.getByRole("switch", { name: "음성 생성" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText("음성 프롬프트")).toHaveValue("quiet ambience matching the dolly motion");
  });

  it("sends the selected Step 1 video quality to source generation", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets(["https://cdn.example/start-1.png"]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4"],
    });
    outputsApi.publishOutputAsset.mockResolvedValueOnce("https://cdn.example/clip-1.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("Video quality"), { target: { value: "1080p" } });
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [new File(["one"], "one.png", { type: "image/png" })],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    await waitFor(() => expect(videoApi.requestSourceGeneration).toHaveBeenCalledWith(expect.objectContaining({
      video_quality: "1080p",
    })));
  });

  it("sends audio settings to the reel group and Kling source generation request", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-audio",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets(["https://cdn.example/start-audio.png"]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-audio",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-audio");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-audio.mp4"],
    });
    outputsApi.publishOutputAsset.mockResolvedValueOnce("https://cdn.example/clip-audio.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);
    render(<MarketingPage />);

    fireEvent.click(screen.getByRole("switch", { name: "음성 생성" }));
    fireEvent.change(screen.getByLabelText("음성 프롬프트"), {
      target: { value: "room tone follows the camera motion" },
    });
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [new File(["one"], "one.png", { type: "image/png" })],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    await waitFor(() => expect(marketingApi.createMarketingReelGroup).toHaveBeenCalledWith(expect.objectContaining({
      audioEnabled: true,
      audioPrompt: "room tone follows the camera motion",
    })));
    expect(videoApi.requestSourceGeneration).toHaveBeenCalledWith(expect.objectContaining({
      sound: "on",
      items: [expect.objectContaining({
        custom_motion_prompt: expect.stringContaining("Audio: room tone follows the camera motion"),
      })],
    }));
  });

  it("locks background scroll while the shared history modal is open", () => {
    render(<MarketingPage />);

    openHistory();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getByRole("button", { name: "닫기" }));

    expect(screen.queryByRole("dialog", { name: "공용 히스토리" })).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
  });

  it("saves, loads, and deletes global prompt history from a modal", async () => {
    marketingApi.saveGlobalPrompt.mockResolvedValueOnce({
      id: "prompt-1",
      global_prompt: "saved warm oak prompt",
      created_at: "2026-05-15T00:00:00Z",
    });
    marketingApi.listGlobalPrompts.mockResolvedValueOnce([
      {
        id: "prompt-2",
        global_prompt: "previous editorial prompt",
        created_at: "2026-05-14T00:00:00Z",
      },
    ]);
    marketingApi.deleteGlobalPrompt.mockResolvedValueOnce({ id: "prompt-2" });
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("Global prompt"), { target: { value: "saved warm oak prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "Global prompt 저장" }));

    await waitFor(() => expect(marketingApi.saveGlobalPrompt).toHaveBeenCalledWith("saved warm oak prompt"));
    expect(await screen.findByText("Global prompt 저장 완료")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Global prompt 가져오기" }));
    expect(await screen.findByRole("dialog", { name: "Global prompt 내역" })).toBeInTheDocument();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    await waitFor(() => expect(marketingApi.deleteGlobalPrompt).toHaveBeenCalledWith("prompt-2"));
    expect(screen.queryByText("previous editorial prompt")).not.toBeInTheDocument();
    expect(screen.getByText("Global prompt 삭제 완료")).toBeInTheDocument();

    marketingApi.listGlobalPrompts.mockResolvedValueOnce([
      {
        id: "prompt-3",
        global_prompt: "previous editorial prompt",
        created_at: "2026-05-14T00:00:00Z",
      },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    expect(document.body.style.overflow).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Global prompt 가져오기" }));
    fireEvent.click(await screen.findByRole("button", { name: /적용: previous editorial prompt/ }));

    expect(screen.getByLabelText("Global prompt")).toHaveValue("previous editorial prompt");
    expect(screen.queryByRole("dialog", { name: "Global prompt 내역" })).not.toBeInTheDocument();
  });

  it("renders start and end frame controls for each selected clip row", () => {
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });

    expect(screen.getAllByText("Start Frame")).toHaveLength(3);
    expect(screen.getAllByText("End Frame")).toHaveLength(3);
    expect(screen.getAllByLabelText(/End Frame 선택/)).toHaveLength(3);
    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeEnabled();
  });

  it("opens Step 1 start and end frames in the shared preview modal", () => {
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "다음 Start 사용" })[0]);

    fireEvent.click(screen.getByRole("button", { name: "Clip 1 Start Frame 확대" }));
    const dialog = screen.getByRole("dialog", { name: "Frame preview" });
    expect(within(dialog).getByText("Clip 1 · Start Frame")).toBeInTheDocument();
    expect(within(dialog).getByAltText("Clip 1 Start Frame 확대")).toHaveAttribute("src", "blob:one.png");

    fireEvent.click(within(dialog).getByRole("button", { name: "다음 프레임" }));
    expect(within(dialog).getByText("Clip 1 · End Frame (Next Start)")).toBeInTheDocument();
    expect(within(dialog).getByAltText("Clip 1 End Frame (Next Start) 확대")).toHaveAttribute("src", "blob:two.png");

    fireEvent.click(within(dialog).getByRole("button", { name: "프레임 미리보기 닫기" }));
    expect(screen.queryByRole("dialog", { name: "Frame preview" })).not.toBeInTheDocument();
  });

  it("saves a Step 1 row prompt with a searchable title", async () => {
    marketingApi.saveClipPrompt.mockResolvedValueOnce({
      id: "clip-prompt-1",
      title: "Window opening",
      prompt: "slow push toward the curtain",
      created_at: "2026-05-17T00:00:00Z",
    });
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.change(screen.getAllByPlaceholderText("이 이미지로 만들고 싶은 장면을 입력하세요.")[0], {
      target: { value: "slow push toward the curtain" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Prompt 저장" })[0]);
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Window opening" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(marketingApi.saveClipPrompt).toHaveBeenCalledWith("Window opening", "slow push toward the curtain"));
    expect(screen.queryByRole("dialog", { name: "Clip prompt 내역" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Clip prompt를 저장했습니다.");
  });

  it("loads and applies a saved Step 1 row prompt by title search", async () => {
    marketingApi.listClipPrompts.mockResolvedValueOnce([
      {
        id: "clip-prompt-1",
        title: "Window opening",
        prompt: "slow push toward the curtain",
        created_at: "2026-05-17T00:00:00Z",
      },
      {
        id: "clip-prompt-2",
        title: "Kitchen pan",
        prompt: "wide move across the island",
        created_at: "2026-05-16T00:00:00Z",
      },
    ]);
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Prompt 가져오기" })[0]);

    expect(await screen.findByRole("dialog", { name: "Clip prompt 내역" })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("제목, 출처 또는 prompt 검색"), { target: { value: "window" } });
    expect(screen.getByRole("button", { name: /적용: Window opening/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /적용: Kitchen pan/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /적용: Window opening/ }));

    expect(screen.getAllByPlaceholderText("이 이미지로 만들고 싶은 장면을 입력하세요.")[0]).toHaveValue("slow push toward the curtain");
    expect(screen.getByRole("status")).toHaveTextContent("Clip prompt를 적용했습니다.");
  });

  it("loads Step 1 prompts from shared history into the row prompt picker", async () => {
    marketingApi.listClipPrompts.mockResolvedValueOnce([]);
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      {
        group_id: "history-final",
        created_at: "2026-05-10T00:00:00Z",
        final_title: "Finished showroom reel",
        clip_count: 2,
        status: "COMPLETED",
      },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-final",
      status: "COMPLETED",
      created_at: "2026-05-10T00:00:00Z",
      updated_at: "2026-05-10T00:00:00Z",
      final_title: "Finished showroom reel",
      global_prompt: "warm showroom prompt",
      platform: "Instagram",
      tone: "Editorial",
      goal: "awareness",
      clips: [
        {
          clip_id: "clip-1",
          client_image_id: "client-1",
          source_image_url: "https://cdn.example/start-1.png",
          generation_mode: "START_ONLY",
          original_order: 1,
          current_order: 1,
          initial_prompt: "history window prompt",
          target_duration_sec: 5,
          approved_attempt_id: null,
          deleted_at: null,
          attempts: [],
        },
        {
          clip_id: "clip-2",
          client_image_id: "client-2",
          source_image_url: "https://cdn.example/start-2.png",
          generation_mode: "START_ONLY",
          original_order: 2,
          current_order: 2,
          initial_prompt: "",
          target_duration_sec: 5,
          approved_attempt_id: null,
          deleted_at: null,
          attempts: [],
        },
      ],
    });
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Prompt 가져오기" })[0]);

    const dialog = await screen.findByRole("dialog", { name: "Clip prompt 내역" });
    fireEvent.click(within(dialog).getByRole("tab", { name: /히스토리 Prompt 1/ }));
    expect(within(dialog).getByText(/완료/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /적용: Finished showroom reel · Clip 1/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /적용: Finished showroom reel · Clip 2/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "삭제" })).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /적용: Finished showroom reel · Clip 1/ }));

    expect(screen.getAllByPlaceholderText("이 이미지로 만들고 싶은 장면을 입력하세요.")[0]).toHaveValue("history window prompt");
    expect(screen.queryByRole("dialog", { name: "Clip prompt 내역" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Clip prompt를 적용했습니다.");
  });

  it("revokes preview object URLs when an image row is removed", async () => {
    render(<MarketingPage />);

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "삭제" })[2]);

    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:three.png"));
  });

  it("renders selected history clips as reference material", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "group-1", created_at: "2026-05-10", clip_count: 1, status: "COMPLETED" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "group-1",
      status: "COMPLETED",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      final_video_url: "https://cdn.example/final.mp4",
      global_prompt: "warm global reference",
      platform: "Instagram",
      tone: "Editorial",
      goal: "awareness",
      clips: [
        {
          clip_id: "clip-1",
          client_image_id: "client-1",
          source_image_url: "https://cdn.example/start.png",
          end_image_url: "https://cdn.example/end.png",
          generation_mode: "START_END",
          original_order: 1,
          current_order: 1,
          initial_prompt: "reference clip prompt",
          target_duration_sec: 5,
          approved_attempt_id: "attempt-1",
          deleted_at: null,
          attempts: [
            {
              attempt_id: "attempt-1",
              clip_id: "clip-1",
              source_job_id: "job-1",
              source_job_item_index: 0,
              prompt: "approved reference prompt",
              duration_sec: 5,
              status: "COMPLETED",
              source_video_url: "https://cdn.example/reference.mp4",
            },
          ],
        },
      ],
    });

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByText("1 clips"));

    expect(await screen.findByText("reference clip prompt")).toBeInTheDocument();
    expect(screen.getByText("approved reference prompt")).toBeInTheDocument();
    expect(screen.getByText("Start Frame")).toBeInTheDocument();
    expect(screen.getByText("End Frame")).toBeInTheDocument();
  });

  it("presents shared history as a searchable restore workspace", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      {
        group_id: "history-final",
        created_at: "2026-05-10T00:00:00Z",
        final_title: "Finished showroom reel",
        final_video_url: "https://cdn.example/final.mp4",
        representative_image_url: "https://cdn.example/thumb.png",
        clip_count: 3,
        status: "COMPLETED",
      },
      {
        group_id: "history-draft",
        created_at: "2026-05-11T00:00:00Z",
        final_title: "Draft review reel",
        clip_count: 3,
        status: "REVIEWING",
      },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-final",
      status: "COMPLETED",
      created_at: "2026-05-10T00:00:00Z",
      updated_at: "2026-05-10T00:00:00Z",
      final_title: "Finished showroom reel",
      final_video_url: "https://cdn.example/final.mp4",
      global_prompt: "warm showroom prompt",
      platform: "Instagram",
      tone: "Editorial",
      goal: "awareness",
      clips: [],
    });

    render(<MarketingPage />);
    openHistory();

    expect(await screen.findByPlaceholderText("제목, 상태, 날짜 검색")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Finished showroom reel/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /히스토리 새로고침/ })).toBeInTheDocument();
    expect(screen.getByText(/마지막 갱신/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("제목, 상태, 날짜 검색"), { target: { value: "showroom" } });

    expect(screen.getByRole("button", { name: /Finished showroom reel/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Draft review reel/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Finished showroom reel/ }));

    expect(await screen.findByRole("heading", { name: "히스토리 상세" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Step 1 설정으로 복원" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Step 2 결과 열기" })).toBeInTheDocument();
    expect(screen.getByText("warm showroom prompt")).toBeInTheDocument();
  });

  it("imports a final history item back into Step 1 draft rows", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "history-1", created_at: "2026-05-10", clip_count: 3, status: "COMPLETED" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-1",
      status: "COMPLETED",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      final_video_url: "https://cdn.example/final.mp4",
      global_prompt: "imported global prompt",
      audio_enabled: true,
      audio_prompt: "imported audio prompt",
      platform: "",
      tone: "",
      goal: "",
      clips: [1, 2, 3].map((order) => ({
        clip_id: `clip-${order}`,
        client_image_id: `client-${order}`,
        source_image_url: `https://cdn.example/start-${order}.png`,
        generation_mode: "START_ONLY",
        original_order: order,
        current_order: order,
        initial_prompt: `imported prompt ${order}`,
        target_duration_sec: 5,
        deleted_at: null,
        attempts: [],
      })),
    });

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /3 clips/ }));
    window.history.pushState({}, "", "/marketing?path=step2&id=stale");
    fireEvent.click(await screen.findByRole("button", { name: "Step 1 설정으로 복원" }));

    expect(screen.queryByRole("dialog", { name: "공용 히스토리" })).not.toBeInTheDocument();
    expect(window.location.search).toBe("");
    expect(screen.getByRole("status")).toHaveTextContent("Step 1 설정을 복원했습니다.");
    expect(screen.getByLabelText("Global prompt")).toHaveValue("imported global prompt");
    expect(screen.getByRole("switch", { name: "음성 생성" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByLabelText("음성 프롬프트")).toHaveValue("imported audio prompt");
    expect(screen.getByDisplayValue("imported prompt 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeEnabled();
  });

  it("resets an imported history workspace back to the initial marketing state", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "history-1", created_at: "2026-05-10", clip_count: 3, status: "COMPLETED" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-1",
      status: "COMPLETED",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      final_video_url: "https://cdn.example/final.mp4",
      global_prompt: "imported global prompt",
      platform: "",
      tone: "",
      goal: "",
      clips: [1, 2, 3].map((order) => ({
        clip_id: `clip-${order}`,
        client_image_id: `client-${order}`,
        source_image_url: `https://cdn.example/start-${order}.png`,
        generation_mode: "START_ONLY",
        original_order: order,
        current_order: order,
        initial_prompt: `imported prompt ${order}`,
        target_duration_sec: 5,
        deleted_at: null,
        attempts: [],
      })),
    });

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /3 clips/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Step 1 설정으로 복원" }));

    expect(screen.getByDisplayValue("imported prompt 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "새 작업 시작" }));

    expect(window.location.search).toBe("");
    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeDisabled();
    expect(screen.getByText(/아직 선택된 사진이 없습니다./)).toBeInTheDocument();
    expect(screen.queryByDisplayValue("imported prompt 1")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Global prompt")).toHaveValue(
      "따뜻한 자연광 속에서 절제된 가구의 디테일을 보여주는 시네마틱 릴스. 부드러운 카메라 무빙, 베이지와 오크 톤.",
    );
    expect(screen.getByRole("status")).toHaveTextContent("새 작업을 시작할 수 있도록 초기화했습니다.");
  });

  it("loads a previous generation directly into Step 2 review", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "history-1", created_at: "2026-05-10", clip_count: 3, status: "REVIEWING" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-1",
      status: "REVIEWING",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      global_prompt: "loaded generation prompt",
      audio_enabled: true,
      audio_prompt: "loaded audio prompt",
      platform: "",
      tone: "",
      goal: "",
      clips: [1, 2, 3].map((order) => ({
        clip_id: `clip-${order}`,
        client_image_id: `client-${order}`,
        source_image_url: `https://cdn.example/start-${order}.png`,
        generation_mode: "START_ONLY",
        original_order: order,
        current_order: order,
        initial_prompt: `loaded prompt ${order}`,
        target_duration_sec: 5,
        deleted_at: null,
        attempts: [{
          attempt_id: `attempt-${order}`,
          clip_id: `clip-${order}`,
          source_job_id: "job-1",
          source_job_item_index: order - 1,
          prompt: `loaded prompt ${order}`,
          duration_sec: 5,
          status: "COMPLETED",
          source_video_url: `https://cdn.example/clip-${order}.mp4`,
        }],
      })),
    });

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /3 clips/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Step 2 결과 열기" }));

    expect(screen.queryByRole("dialog", { name: "공용 히스토리" })).not.toBeInTheDocument();
    expect(window.location.search).toBe("?path=step2&id=history-1");
    expect(screen.getByRole("status")).toHaveTextContent("Step 2 결과를 열었습니다.");
    expect(screen.getByRole("heading", { name: "2. 비디오 확인" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "최종 합치기 음성 유지" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getAllByText("loaded prompt 1").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("switch", { name: "최종 합치기 음성 유지" }));
    await waitFor(() => expect(marketingApi.updateMarketingAudioSettings).toHaveBeenCalledWith("history-1", {
      audioEnabled: false,
      audioPrompt: "loaded audio prompt",
    }));
    expect(screen.getByRole("switch", { name: "최종 합치기 음성 유지" })).toHaveAttribute("aria-checked", "false");
  });

  it("restores Step 2 review state from the marketing query string on reload", async () => {
    window.history.pushState({}, "", "/marketing?path=step2&id=history-1");
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce(step2HistoryDetail("history-1"));

    render(<MarketingPage />);

    expect(screen.getByRole("status")).toHaveTextContent("저장된 Step 2를 불러오는 중...");
    await waitFor(() => expect(marketingApi.getMarketingReelGroup).toHaveBeenCalledWith("history-1"));
    expect(await screen.findByRole("heading", { name: "2. 비디오 확인" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "최종 합치기 음성 유지" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getAllByText("loaded prompt 1").length).toBeGreaterThan(0);
    expect(screen.getByText("loaded prompt 2")).toBeInTheDocument();
  });

  it("does not treat restored completed attempts as approved when the API has no approval", async () => {
    window.history.pushState({}, "", "/marketing?path=step2&id=history-unapproved");
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce(step2HistoryDetail("history-unapproved"));

    render(<MarketingPage />);

    await waitFor(() => expect(marketingApi.getMarketingReelGroup).toHaveBeenCalledWith("history-unapproved"));
    expect(await screen.findByRole("heading", { name: "2. 비디오 확인" })).toBeInTheDocument();
    const prepareButton = screen.getByRole("button", { name: "승인본으로 합치기 준비" });
    expect(prepareButton).toBeDisabled();
    fireEvent.click(prepareButton);
    expect(videoApi.requestMarketingCompile).not.toHaveBeenCalled();
  });

  it("keeps Step 1 active and shows an error when query Step 2 restore fails", async () => {
    window.history.pushState({}, "", "/marketing?path=step2&id=missing-history");
    marketingApi.getMarketingReelGroup.mockRejectedValueOnce(new Error("마케팅 릴스 상세 조회 실패"));

    render(<MarketingPage />);

    await waitFor(() => expect(marketingApi.getMarketingReelGroup).toHaveBeenCalledWith("missing-history"));
    expect(screen.getByRole("heading", { name: "1. 생성 전" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("마케팅 릴스 상세 조회 실패");
  });

  it("restores the same Step 2 id again when browser history returns to its query", async () => {
    window.history.pushState({}, "", "/marketing?path=step2&id=history-1");
    marketingApi.getMarketingReelGroup
      .mockResolvedValueOnce(step2HistoryDetail("history-1"))
      .mockResolvedValueOnce(step2HistoryDetail("history-1"));

    render(<MarketingPage />);

    expect(await screen.findByRole("heading", { name: "2. 비디오 확인" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "이 데이터로 작업 시작" }));
    expect(window.location.search).toBe("");

    window.history.pushState({}, "", "/marketing?path=step2&id=history-1");
    window.dispatchEvent(new PopStateEvent("popstate"));

    await waitFor(() => expect(marketingApi.getMarketingReelGroup).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("heading", { name: "2. 비디오 확인" })).toBeInTheDocument();
  });

  it("restores the saved landscape aspect ratio when history opens Step 2", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "history-landscape", created_at: "2026-05-10", aspect_ratio: "16:9", video_quality: "1080p", clip_count: 1, status: "REVIEWING" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-landscape",
      status: "REVIEWING",
      aspect_ratio: "16:9",
      video_quality: "1080p",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      global_prompt: "loaded landscape prompt",
      platform: "",
      tone: "",
      goal: "",
      clips: [{
        clip_id: "clip-landscape",
        client_image_id: "client-landscape",
        source_image_url: "https://cdn.example/start-landscape.png",
        generation_mode: "START_ONLY",
        original_order: 1,
        current_order: 1,
        initial_prompt: "loaded landscape prompt",
        target_duration_sec: 5,
        deleted_at: null,
        attempts: [{
          attempt_id: "attempt-landscape",
          clip_id: "clip-landscape",
          source_job_id: "job-landscape",
          source_job_item_index: 0,
          prompt: "loaded landscape prompt",
          duration_sec: 5,
          status: "COMPLETED",
          source_video_url: "https://cdn.example/clip-landscape.mp4",
        }],
      }],
    });

    const { container } = render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /1 clips/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Step 2 결과 열기" }));

    expect(screen.getByRole("heading", { name: "2. 비디오 확인" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Clip 1 생성 설정")).getByText("16:9 가로")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Clip 1 생성 설정")).getByText("1080p")).toBeInTheDocument();
    expect(container.querySelector(".clip-review-media-row")?.querySelectorAll(".ratio-16-9")).toHaveLength(3);
  });

  it("opens Step 2 start and end frames in a navigable preview modal", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "history-preview", created_at: "2026-05-10", aspect_ratio: "16:9", video_quality: "1080p", clip_count: 2, status: "REVIEWING" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-preview",
      status: "REVIEWING",
      aspect_ratio: "16:9",
      video_quality: "1080p",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      global_prompt: "preview prompt",
      platform: "",
      tone: "",
      goal: "",
      clips: [
        {
          clip_id: "clip-1",
          client_image_id: "client-1",
          source_image_url: "https://cdn.example/start-1.png",
          end_image_url: "https://cdn.example/end-1.png",
          generation_mode: "START_END",
          original_order: 1,
          current_order: 1,
          initial_prompt: "clip 1 prompt",
          target_duration_sec: 5,
          deleted_at: null,
          attempts: [{
            attempt_id: "attempt-1",
            clip_id: "clip-1",
            source_job_id: "job-1",
            source_job_item_index: 0,
            prompt: "clip 1 prompt",
            duration_sec: 5,
            status: "COMPLETED",
            source_video_url: "https://cdn.example/video-1.mp4",
          }],
        },
        {
          clip_id: "clip-2",
          client_image_id: "client-2",
          source_image_url: "https://cdn.example/start-2.png",
          generation_mode: "START_ONLY",
          original_order: 2,
          current_order: 2,
          initial_prompt: "clip 2 prompt",
          target_duration_sec: 5,
          deleted_at: null,
          attempts: [{
            attempt_id: "attempt-2",
            clip_id: "clip-2",
            source_job_id: "job-1",
            source_job_item_index: 1,
            prompt: "clip 2 prompt",
            duration_sec: 5,
            status: "COMPLETED",
            source_video_url: "https://cdn.example/video-2.mp4",
          }],
        },
      ],
    });

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /2 clips/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Step 2 결과 열기" }));
    fireEvent.click(screen.getByRole("button", { name: "Clip 1 Start Frame 확대" }));

    const dialog = screen.getByRole("dialog", { name: "Frame preview" });
    expect(document.body.style.overflow).toBe("hidden");
    expect(within(dialog).getByText("Clip 1 · Start Frame")).toBeInTheDocument();
    expect(within(dialog).getByAltText("Clip 1 Start Frame 확대")).toHaveAttribute("src", "https://cdn.example/start-1.png");

    fireEvent.click(within(dialog).getByRole("button", { name: "다음 프레임" }));
    expect(within(dialog).getByText("Clip 1 · End Frame")).toBeInTheDocument();
    expect(within(dialog).getByAltText("Clip 1 End Frame 확대")).toHaveAttribute("src", "https://cdn.example/end-1.png");

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(within(dialog).getByText("Clip 2 · Start Frame")).toBeInTheDocument();
    expect(within(dialog).getByAltText("Clip 2 Start Frame 확대")).toHaveAttribute("src", "https://cdn.example/start-2.png");

    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(within(dialog).getByText("Clip 1 · End Frame")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Frame preview" })).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
  });

  it("updates a selected history title", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "group-title", created_at: "2026-05-10", final_title: "old title", clip_count: 1, status: "COMPLETED" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "group-title",
      status: "COMPLETED",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      final_title: "old title",
      final_video_url: "https://cdn.example/final.mp4",
      global_prompt: "warm global reference",
      platform: "Instagram",
      tone: "Editorial",
      goal: "awareness",
      clips: [],
    });
    marketingApi.updateMarketingReelGroupTitle.mockResolvedValueOnce({
      group_id: "group-title",
      final_title: "new title",
    });

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /old title/ }));
    fireEvent.change(await screen.findByDisplayValue("old title"), { target: { value: "new title" } });
    fireEvent.click(screen.getByRole("button", { name: "제목 저장" }));

    await waitFor(() => expect(marketingApi.updateMarketingReelGroupTitle).toHaveBeenCalledWith("group-title", "new title"));
    expect(await screen.findByText("new title")).toBeInTheDocument();
  });

  it("persists Step 2 clip deletion before removing it from the active sequence", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets([
      "https://cdn.example/start-1.png",
      "https://cdn.example/start-2.png",
      "https://cdn.example/start-3.png",
    ]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4", "/outputs/clip-2.mp4", "/outputs/clip-3.mp4"],
    });
    outputsApi.publishOutputAsset
      .mockResolvedValueOnce("https://cdn.example/clip-1.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-2.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-3.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);
    marketingApi.deleteMarketingReelClip.mockResolvedValueOnce({
      group_id: "group-1",
      clip_id: "clip-1",
      deleted_at: "2026-05-10T00:00:00+09:00",
    });

    render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("Video ratio"), { target: { value: "16:9" } });
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    expect(await screen.findByText("Clip 1")).toBeInTheDocument();
    expect(within(screen.getByLabelText("Clip 1 생성 설정")).getByText("16:9 가로")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "순서에서 삭제" })[0]);

    await waitFor(() => expect(marketingApi.deleteMarketingReelClip).toHaveBeenCalledWith("group-1", "clip-1"));
    await waitFor(() => expect(screen.getAllByRole("button", { name: "순서에서 삭제" })).toHaveLength(2));
  });

  it("explains locked Step 1 and restores current data into an editable Step 1 draft", async () => {
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets([
      "https://cdn.example/start-1.png",
      "https://cdn.example/start-2.png",
      "https://cdn.example/start-3.png",
    ]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4", "/outputs/clip-2.mp4", "/outputs/clip-3.mp4"],
    });
    outputsApi.publishOutputAsset
      .mockResolvedValueOnce("https://cdn.example/clip-1.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-2.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-3.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);

    render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("Video quality"), { target: { value: "1080p" } });
    fireEvent.click(screen.getByRole("switch", { name: "음성 생성" }));
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    expect(await screen.findByText("Clip 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /1생성 전/i }));

    expect(alertSpy).toHaveBeenCalledWith("이 데이터로 작업 시작 버튼 선택시 설정을 수정할 수 있습니다");
    await waitFor(() => expect(screen.getByText(/생성 그룹이 만들어진 뒤에는/)).toBeInTheDocument());
    expect(screen.queryByLabelText("Content type")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Video ratio")).toBeDisabled();
    expect(screen.getByDisplayValue(/따뜻한 자연광 속에서/)).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "삭제" }).every((button) => button.hasAttribute("disabled"))).toBe(true);
    expect(screen.getAllByRole("button", { name: /이미지 위로 이동/ }).every((button) => button.hasAttribute("disabled"))).toBe(true);
    expect(screen.getAllByRole("button", { name: /이미지 아래로 이동/ }).every((button) => button.hasAttribute("disabled"))).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "이 데이터로 작업 시작" }));

    expect(screen.queryByText(/생성 그룹이 만들어진 뒤에는/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Video ratio")).toBeEnabled();
    expect(screen.getByDisplayValue(/따뜻한 자연광 속에서/)).toBeEnabled();
    expect(screen.getAllByRole("button", { name: "삭제" }).every((button) => !button.hasAttribute("disabled"))).toBe(true);
    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /2비디오 확인/i })).toBeDisabled();
    alertSpy.mockRestore();
  });

  it("unlocks Step 1 for a fresh retry when setup fails before source generation starts", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-setup-failed",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockRejectedValueOnce(new Error("upload failed"));
    marketingApi.markMarketingReelGroupFailed.mockResolvedValueOnce({ group_id: "group-setup-failed" });

    render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("Video quality"), { target: { value: "1080p" } });
    fireEvent.click(screen.getByRole("switch", { name: "음성 생성" }));
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    await waitFor(() => expect(screen.getByText("upload failed")).toBeInTheDocument());
    expect(marketingApi.markMarketingReelGroupFailed).toHaveBeenCalledWith("group-setup-failed");
    expect(videoApi.requestSourceGeneration).not.toHaveBeenCalled();
    expect(screen.queryByText(/생성 그룹이 만들어진 뒤에는/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeEnabled();
    expect(screen.getByLabelText("이미지 선택")).toBeEnabled();
  });

  it("does not mark the whole group failed after source generation has started", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets([
      "https://cdn.example/start-1.png",
      "https://cdn.example/start-2.png",
      "https://cdn.example/start-3.png",
    ]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4", "/outputs/clip-2.mp4", "/outputs/clip-3.mp4"],
    });
    outputsApi.publishOutputAsset.mockResolvedValue("https://cdn.example/clip.mp4");
    marketingApi.updateMarketingClipAttempt.mockRejectedValueOnce(new Error("attempt save failed"));

    render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    await waitFor(() => expect(screen.getByText(/attempt save failed/)).toBeInTheDocument());
    expect(marketingApi.markMarketingReelGroupFailed).not.toHaveBeenCalled();
  });

  it("continues finalizing later source clips when one attempt update fails", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets([
      "https://cdn.example/start-1.png",
      "https://cdn.example/start-2.png",
      "https://cdn.example/start-3.png",
    ]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4", "/outputs/clip-2.mp4", "/outputs/clip-3.mp4"],
    });
    outputsApi.publishOutputAsset
      .mockResolvedValueOnce("https://cdn.example/clip-1.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-2.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-3.mp4");
    marketingApi.updateMarketingClipAttempt
      .mockRejectedValueOnce(new Error("attempt save failed"))
      .mockImplementation(async (_groupId, _attemptId, payload) => payload);

    render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    await waitFor(() => expect(marketingApi.updateMarketingClipAttempt).toHaveBeenCalledTimes(3));
    expect(screen.getByText(/일부 attempt 저장 실패/)).toBeInTheDocument();
    expect(screen.getAllByText("FAILED").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("COMPLETED").length).toBeGreaterThanOrEqual(2);
    expect(marketingApi.markMarketingReelGroupFailed).not.toHaveBeenCalled();
  });

  it("continues polling and recovers final attempts when an initial attempt save fails", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets([
      "https://cdn.example/start-1.png",
      "https://cdn.example/start-2.png",
      "https://cdn.example/start-3.png",
    ]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt
      .mockRejectedValueOnce(new Error("running save failed"))
      .mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4", "/outputs/clip-2.mp4", "/outputs/clip-3.mp4"],
    });
    outputsApi.publishOutputAsset
      .mockResolvedValueOnce("https://cdn.example/clip-1.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-2.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-3.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);

    render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    await waitFor(() => expect(videoApi.fetchVideoJobStatus).toHaveBeenCalledWith("job-1"));
    await waitFor(() => expect(marketingApi.createMarketingClipAttempt).toHaveBeenCalledTimes(4));
    expect(marketingApi.updateMarketingClipAttempt).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/running save failed/)).not.toBeInTheDocument();
    expect(screen.getAllByText("COMPLETED").length).toBeGreaterThanOrEqual(3);
    expect(marketingApi.markMarketingReelGroupFailed).not.toHaveBeenCalled();
  });

  it("shows compile status API progress in the final merge step", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets([
      "https://cdn.example/start-1.png",
      "https://cdn.example/start-2.png",
      "https://cdn.example/start-3.png",
    ]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("source-job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus
      .mockResolvedValueOnce({
        status: "COMPLETED",
        progress: 100,
        results: ["/outputs/clip-1.mp4", "/outputs/clip-2.mp4", "/outputs/clip-3.mp4"],
      })
      .mockResolvedValueOnce({
        status: "RUNNING",
        progress: 40,
        message: "Merging clips...",
      })
      .mockResolvedValueOnce({
        status: "COMPLETED",
        progress: 100,
        result_url: "/outputs/final.mp4",
      });
    outputsApi.publishOutputAsset
      .mockResolvedValueOnce("https://cdn.example/clip-1.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-2.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-3.mp4")
      .mockResolvedValueOnce("https://cdn.example/final.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);
    marketingApi.approveMarketingClipAttempt.mockResolvedValue({ group_id: "group-1" });
    videoApi.requestMarketingCompile.mockResolvedValueOnce("local-compile-job-1");
    marketingApi.patchMarketingFinalResult.mockResolvedValueOnce({ group_id: "group-1" });

    render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("Video quality"), { target: { value: "1080p" } });
    fireEvent.click(screen.getByRole("switch", { name: "음성 생성" }));
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    await waitFor(() => expect(screen.getAllByRole("button", { name: "승인" })).toHaveLength(3));
    screen.getAllByRole("button", { name: "승인" }).forEach((button) => fireEvent.click(button));
    await waitFor(() => expect(screen.getByRole("button", { name: "승인본으로 합치기 준비" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "승인본으로 합치기 준비" }));
    await screen.findByRole("button", { name: "최종 영상 합치기" });
    fireEvent.click(screen.getByRole("button", { name: "최종 영상 합치기" }));

    await waitFor(() => expect(screen.getByText("최종 영상 합치기: Merging clips...")).toBeInTheDocument());
    expect(videoApi.requestMarketingCompile).toHaveBeenCalledWith(expect.objectContaining({
      clips: [
        expect.objectContaining({ video_url: "https://cdn.example/clip-1.mp4" }),
        expect.objectContaining({ video_url: "https://cdn.example/clip-2.mp4" }),
        expect.objectContaining({ video_url: "https://cdn.example/clip-3.mp4" }),
      ],
      aspect_ratio: "9:16",
      aspect_mode: "crop",
      video_quality: "1080p",
      preserve_audio: true,
    }));
    expect(videoApi.requestCompile).not.toHaveBeenCalled();
    const finalStep = screen.getByRole("heading", { name: "3. 최종 합치기" }).closest("section") as HTMLElement;
    expect(within(finalStep).getByLabelText("Final merge progress").querySelector(".progress-bar")).toHaveStyle({ width: "48%" });

    await waitFor(() => expect(marketingApi.patchMarketingFinalResult).toHaveBeenCalled(), { timeout: 3000 });
  });

  it("routes a single approved clip through the local threaded compile API before saving the final video", async () => {
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets(["https://cdn.example/start-1.png"]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("source-job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus
      .mockResolvedValueOnce({
        status: "COMPLETED",
        progress: 100,
        results: ["/outputs/clip-1.mp4"],
      })
      .mockResolvedValueOnce({
        status: "COMPLETED",
        progress: 100,
        result_url: "/outputs/final-single.mp4",
      });
    outputsApi.publishOutputAsset
      .mockResolvedValueOnce("https://cdn.example/clip-1.mp4")
      .mockResolvedValueOnce("https://cdn.example/final-single.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);
    marketingApi.approveMarketingClipAttempt.mockResolvedValue({ group_id: "group-1" });
    videoApi.requestMarketingCompile.mockResolvedValueOnce("local-compile-single-1");
    marketingApi.patchMarketingFinalResult.mockResolvedValueOnce({ group_id: "group-1" });

    const { container } = render(<MarketingPage />);
    fireEvent.change(screen.getByLabelText("Video ratio"), { target: { value: "16:9" } });
    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [new File(["one"], "one.png", { type: "image/png" })],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    const approveButton = await screen.findByRole("button", { name: "승인" });
    fireEvent.click(approveButton);
    await waitFor(() => expect(screen.getByRole("button", { name: "승인본으로 합치기 준비" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "승인본으로 합치기 준비" }));
    expect(await screen.findByText("승인한 source clips를 현재 순서대로 하나의 최종 영상으로 합칩니다.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "최종 영상 합치기" }));

    await waitFor(() => expect(videoApi.requestMarketingCompile).toHaveBeenCalledWith(expect.objectContaining({
      clips: [expect.objectContaining({ video_url: "https://cdn.example/clip-1.mp4" })],
      aspect_ratio: "16:9",
      aspect_mode: "crop",
    })));
    expect(videoApi.requestCompile).not.toHaveBeenCalled();

    await waitFor(() => expect(marketingApi.patchMarketingFinalResult).toHaveBeenCalledWith("group-1", expect.objectContaining({
      compile_job_id: "local-compile-single-1",
      final_video_url: "https://cdn.example/final-single.mp4",
      selected_attempt_ids: [expect.any(String)],
      compile_payload_summary: expect.not.objectContaining({ mode: "single_clip_passthrough" }),
    })));
    expect(screen.getByText("최종 릴스가 준비되었습니다.")).toBeInTheDocument();
    expect(screen.getByLabelText("Final Reel")).toBeInTheDocument();
    expect(container.querySelector(".final-video")?.classList.contains("ratio-16-9")).toBe(true);
  });

  it("uses an explicitly selected history clip as the regeneration reference", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "history-1", created_at: "2026-05-10", clip_count: 2, status: "COMPLETED" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-1",
      status: "COMPLETED",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      final_video_url: "https://cdn.example/final.mp4",
      global_prompt: "warm global reference",
      platform: "Instagram",
      tone: "Editorial",
      goal: "awareness",
      clips: [
        {
          clip_id: "history-clip-1",
          client_image_id: "history-client-1",
          source_image_url: "https://cdn.example/start-1.png",
          generation_mode: "START_ONLY",
          original_order: 1,
          current_order: 1,
          initial_prompt: "first reference prompt",
          target_duration_sec: 5,
          approved_attempt_id: "history-attempt-1",
          deleted_at: null,
          attempts: [
            {
              attempt_id: "history-attempt-1",
              clip_id: "history-clip-1",
              source_job_id: "job-1",
              source_job_item_index: 0,
              prompt: "first attempt prompt",
              duration_sec: 5,
              status: "COMPLETED",
              source_video_url: "https://cdn.example/reference-1.mp4",
            },
          ],
        },
        {
          clip_id: "history-clip-2",
          client_image_id: "history-client-2",
          source_image_url: "https://cdn.example/start-2.png",
          generation_mode: "START_ONLY",
          original_order: 2,
          current_order: 2,
          initial_prompt: "second reference prompt",
          target_duration_sec: 5,
          approved_attempt_id: "history-attempt-2",
          deleted_at: null,
          attempts: [
            {
              attempt_id: "history-attempt-2",
              clip_id: "history-clip-2",
              source_job_id: "job-2",
              source_job_item_index: 1,
              prompt: "second attempt prompt",
              duration_sec: 5,
              status: "COMPLETED",
              source_video_url: "https://cdn.example/reference-2.mp4",
            },
          ],
        },
      ],
    });
    marketingApi.createMarketingReelGroup.mockImplementationOnce(async (payload) => ({
      group_id: "group-1",
      clips: payload.clips.map((clip, index) => ({
        clip_id: `clip-${index + 1}`,
        client_image_id: clip.clientImageId,
      })),
    }));
    outputsApi.uploadOutputImageAssets.mockResolvedValueOnce(uploadedAssets([
      "https://cdn.example/start-1.png",
      "https://cdn.example/start-2.png",
      "https://cdn.example/start-3.png",
    ]));
    marketingApi.updateMarketingClipSourceImages.mockImplementationOnce(async (_groupId, payload) => ({
      group_id: "group-1",
      clips: payload.clips,
    }));
    videoApi.requestSourceGeneration.mockResolvedValueOnce("job-1");
    marketingApi.createMarketingClipAttempt.mockImplementation(async (_groupId, payload) => payload);
    videoApi.fetchVideoJobStatus.mockResolvedValueOnce({
      status: "COMPLETED",
      progress: 100,
      results: ["/outputs/clip-1.mp4", "/outputs/clip-2.mp4", "/outputs/clip-3.mp4"],
    });
    outputsApi.publishOutputAsset
      .mockResolvedValueOnce("https://cdn.example/clip-1.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-2.mp4")
      .mockResolvedValueOnce("https://cdn.example/clip-3.mp4");
    marketingApi.updateMarketingClipAttempt.mockImplementation(async (_groupId, _attemptId, payload) => payload);

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /2 clips/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Clip 2 레퍼런스로 선택" }));

    fireEvent.change(screen.getByLabelText("이미지 선택"), {
      target: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
          new File(["three"], "three.png", { type: "image/png" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /1차 비디오 생성/i }));

    expect(await screen.findByText("Clip 1")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "재생성 설정" })[0]);

    const regenerationPanel = (await screen.findByRole("button", { name: "이 prompt로 재생성" })).closest("section") as HTMLElement;
    const regenerationReferenceLabel = within(regenerationPanel).getByText("선택한 레퍼런스");
    const regenerationReferenceCard = regenerationReferenceLabel.closest("article") as HTMLElement;
    expect(within(regenerationReferenceCard).getByText("second reference prompt")).toBeInTheDocument();
    expect(within(regenerationReferenceCard).queryByText("first reference prompt")).not.toBeInTheDocument();
  });

  it("uses playable history attempts as references when approved history data is stale", async () => {
    marketingApi.listMarketingReelGroups.mockResolvedValueOnce([
      { group_id: "history-stale", created_at: "2026-05-10", clip_count: 1, status: "COMPLETED" },
    ]);
    marketingApi.getMarketingReelGroup.mockResolvedValueOnce({
      group_id: "history-stale",
      status: "COMPLETED",
      created_at: "2026-05-10",
      updated_at: "2026-05-10",
      global_prompt: "stale reference group",
      platform: "Instagram",
      tone: "Editorial",
      goal: "awareness",
      clips: [
        {
          clip_id: "history-clip-1",
          client_image_id: "history-client-1",
          source_image_url: "https://cdn.example/start-1.png",
          generation_mode: "START_ONLY",
          original_order: 1,
          current_order: 1,
          initial_prompt: "history clip prompt",
          target_duration_sec: 5,
          approved_attempt_id: "stale-approved",
          deleted_at: null,
          attempts: [
            {
              attempt_id: "stale-approved",
              clip_id: "history-clip-1",
              source_job_id: "job-1",
              source_job_item_index: 0,
              prompt: "stale approved prompt",
              duration_sec: 5,
              status: "COMPLETED",
            },
            {
              attempt_id: "playable-attempt",
              clip_id: "history-clip-1",
              source_job_id: "job-2",
              source_job_item_index: 0,
              prompt: "playable attempt prompt",
              duration_sec: 5,
              status: "COMPLETED",
              source_video_url: "https://cdn.example/playable.mp4",
            },
          ],
        },
      ],
    });

    render(<MarketingPage />);
    openHistory();
    fireEvent.click(await screen.findByRole("button", { name: /1 clips/ }));

    expect(await screen.findByText("playable attempt prompt")).toBeInTheDocument();
    expect(screen.queryByText("stale approved prompt")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clip 1 레퍼런스로 선택" })).toBeEnabled();
  });
});
