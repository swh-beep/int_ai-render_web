import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { VideoStudioPage } from "./VideoStudioPage";

describe("VideoStudioPage", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/app/video-studio");
  });

  it("uses the legacy Video Studio menu, clip workspace, and assemble editor structure", () => {
    render(<VideoStudioPage />);

    expect(document.body.dataset.page).toBe("video-studio");
    expect(document.getElementById("menu-screen")).toHaveClass("is-main-layout");
    expect(document.querySelector(".is-branding-wordmark")).toHaveAttribute("src", "/static/TIOR STUDIO(Black).png");
    expect(document.querySelector("#btn-feature-2")).toHaveTextContent("Assemble Full Video");

    fireEvent.click(screen.getByRole("button", { name: /Create Video Clips/i }));
    expect(document.querySelector("#workspace-feature-1 .clip-workspace-shell")).toBeInTheDocument();
    expect(document.querySelector("#clip-ref-drop-zone.clip-upload-box")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Back/i }));
    fireEvent.click(screen.getByRole("button", { name: /Assemble Full Video/i }));
    expect(document.querySelector("#workspace-feature-2.assemble-editor-workspace")).toBeInTheDocument();
    expect(document.querySelector(".assemble-monitor-frame")).toBeInTheDocument();
    expect(document.querySelector(".assemble-inspector-panel")).toBeInTheDocument();
  });

  it("routes each video workspace and responds to browser history changes", async () => {
    render(<VideoStudioPage />);

    fireEvent.click(screen.getByRole("button", { name: "Create Video Clips" }));
    expect(window.location.pathname).toBe("/app/video-studio/create-video-clips");

    window.history.pushState({}, "", "/app/video-studio");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(window.location.pathname).toBe("/app/video-studio");
    await waitFor(() => expect(document.getElementById("menu-screen")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Assemble Full Video" }));
    expect(window.location.pathname).toBe("/app/video-studio/assemble-full-video");

    fireEvent.click(screen.getByRole("button", { name: /Back/i }));
    fireEvent.click(screen.getByRole("button", { name: "Post-Production" }));
    expect(window.location.pathname).toBe("/app/video-studio/post-production");
  });

  it("shows validation feedback when source clip generation starts without an image", async () => {
    render(<VideoStudioPage />);

    fireEvent.click(screen.getByRole("button", { name: /Create Video Clips/i }));
    fireEvent.click(screen.getByRole("button", { name: /Generate Clips/i }));

    expect(await screen.findByText(/Failed: Upload one source image./)).toBeInTheDocument();
  });

  it("shows validation feedback when exporting without assemble clips", async () => {
    render(<VideoStudioPage />);

    fireEvent.click(screen.getByRole("button", { name: /Assemble Full Video/i }));
    fireEvent.click(screen.getByRole("button", { name: /Export Sequence/i }));

    expect(await screen.findByText(/Failed: Upload at least one clip to assemble./)).toBeInTheDocument();
  });
});
