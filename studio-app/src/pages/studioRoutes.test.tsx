import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "../App";

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

describe("studio app migrated routes", () => {
  it("renders the React Image Studio page instead of a placeholder", () => {
    renderAt("/app/image-studio");

    expect(screen.getByRole("heading", { name: "Image Studio" })).toBeInTheDocument();
    expect(screen.getByText("Generate Real Photo")).toBeInTheDocument();
    expect(screen.getByText("Edit Image")).toBeInTheDocument();
    expect(screen.getByText("Decorate Image")).toBeInTheDocument();
    expect(screen.queryByText(/placeholder/i)).not.toBeInTheDocument();
  });

  it("renders the React Video Studio page instead of a placeholder", () => {
    renderAt("/app/video-studio");

    expect(screen.getByRole("heading", { name: "Video Studio" })).toBeInTheDocument();
    expect(screen.getByText("Create Video Clips")).toBeInTheDocument();
    expect(screen.getByText("Assemble Full Video")).toBeInTheDocument();
    expect(screen.getByText("Post-Production")).toBeInTheDocument();
    expect(screen.queryByText(/placeholder/i)).not.toBeInTheDocument();
  });

  it("renders direct Image Studio workspace routes", () => {
    renderAt("/app/image-studio/generate-real-photo");
    expect(screen.getByRole("heading", { name: "Generate Real Photo" })).toBeInTheDocument();
  });

  it("renders direct Video Studio workspace routes", () => {
    renderAt("/app/video-studio/assemble-full-video");
    expect(screen.getByText("No active shot")).toBeInTheDocument();
    expect(screen.getByText("Clip Controls")).toBeInTheDocument();
  });
});
