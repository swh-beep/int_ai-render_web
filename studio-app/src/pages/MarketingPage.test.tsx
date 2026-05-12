import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const marketingApi = vi.hoisted(() => ({
  approveMarketingClipAttempt: vi.fn(),
  createMarketingClipAttempt: vi.fn(),
  createMarketingReelGroup: vi.fn(),
  deleteMarketingReelClip: vi.fn(),
  getMarketingReelGroup: vi.fn(),
  listMarketingReelGroups: vi.fn(),
  markMarketingReelGroupFailed: vi.fn(),
  patchMarketingFinalResult: vi.fn(),
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
  requestSourceGeneration: vi.fn(),
}));

vi.mock("../api/marketingReels", () => marketingApi);
vi.mock("../api/outputs", () => outputsApi);
vi.mock("../api/videoMvp", () => videoApi);

import { MarketingPage } from "./MarketingPage";

function uploadedAssets(urls: string[]) {
  return urls.map((url) => ({ publicUrl: url, readUrl: url }));
}

describe("MarketingPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn((file: File) => `blob:${file.name}`),
      revokeObjectURL: vi.fn(),
    });
  });

  it("keeps source generation disabled until at least three images are selected", () => {
    render(<MarketingPage />);

    expect(screen.getByRole("button", { name: /1차 비디오 생성/i })).toBeDisabled();
    expect(screen.getByText(/아직 선택된 사진이 없습니다./)).toBeInTheDocument();
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
    expect(screen.getByLabelText("Content type")).toBeInTheDocument();
    expect(screen.getByLabelText("이미지 선택")).toBeInTheDocument();
    expect(screen.getByText("공용 히스토리")).toBeInTheDocument();
    expect(screen.getByText("Kling payload preview")).toBeInTheDocument();
    expect(screen.getByText("Hook")).toBeInTheDocument();
    expect(screen.getByText("Caption")).toBeInTheDocument();
    expect(screen.getByText("CTA")).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
    fireEvent.click(await screen.findByText("1 clips"));

    expect(await screen.findByText("reference clip prompt")).toBeInTheDocument();
    expect(screen.getByText("approved reference prompt")).toBeInTheDocument();
    expect(screen.getByText("Start Frame")).toBeInTheDocument();
    expect(screen.getByText("End Frame")).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
    fireEvent.click(await screen.findByText("old title"));
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

  it("locks Step 1 structural edits after a generation group is created", async () => {
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

    await waitFor(() => expect(screen.getByText(/생성 그룹이 만들어진 뒤에는/)).toBeInTheDocument());
    expect(screen.getByLabelText("Content type")).toBeDisabled();
    expect(screen.getByDisplayValue("신상 라운지 컬렉션 인지도 확대")).toBeDisabled();
    expect(screen.getByDisplayValue(/따뜻한 자연광 속에서/)).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "삭제" }).every((button) => button.hasAttribute("disabled"))).toBe(true);
    expect(screen.getAllByRole("button", { name: "위" }).every((button) => button.hasAttribute("disabled"))).toBe(true);
    expect(screen.getAllByRole("button", { name: "아래" }).every((button) => button.hasAttribute("disabled"))).toBe(true);
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
    fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
    fireEvent.click(await screen.findByText("2 clips"));
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
    fireEvent.click(screen.getAllByRole("button", { name: "재생성" })[0]);

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
    fireEvent.click(screen.getByRole("button", { name: "새로고침" }));
    fireEvent.click(await screen.findByText("1 clips"));

    expect(await screen.findByText("playable attempt prompt")).toBeInTheDocument();
    expect(screen.queryByText("stale approved prompt")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clip 1 레퍼런스로 선택" })).toBeEnabled();
  });
});
