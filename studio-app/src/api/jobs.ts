export type QueuedJobResponse = {
  job_id?: string;
  error?: string;
};

export type ImageJobState = {
  status?: string;
  message?: string;
  error?: string;
  urls?: string[];
};

export async function fetchImageJobStatus(jobId: string): Promise<ImageJobState> {
  const response = await fetch(`/jobs/${jobId}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`상태 확인 실패 (${response.status})`);
  }
  return response.json() as Promise<ImageJobState>;
}

export async function pollImageJob(
  jobId: string,
  onStatus: (message: string) => void,
  intervalMs = 2000,
): Promise<ImageJobState> {
  for (;;) {
    const state = await fetchImageJobStatus(jobId);
    onStatus(state.message ?? state.status ?? "Working");
    if (state.status === "finished" || state.status === "COMPLETED") return state;
    if (state.status === "failed" || state.status === "FAILED") throw new Error(state.error ?? "Image generation failed.");
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
}
