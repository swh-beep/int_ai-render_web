import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ImageStudioPage } from "./ImageStudioPage";

describe("ImageStudioPage", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/app/image-studio");
  });

  it("uses the legacy Image Studio menu and workspace structure", () => {
    render(<ImageStudioPage />);

    expect(document.body.dataset.page).toBe("image-studio");
    expect(document.getElementById("menu-screen")).toHaveClass("is-main-layout");
    expect(document.querySelector(".is-branding-logo")).toHaveAttribute("src", "/static/logo.png");
    expect(document.querySelector(".card-hero")).toHaveTextContent("Generate Real Photo");

    fireEvent.click(screen.getByRole("button", { name: /Edit Image/i }));

    expect(document.getElementById("workspace-feature-2")).toHaveClass("is-workspace");
    expect(document.querySelector("#workspace-feature-2 .is-workspace-card")).toBeInTheDocument();
    expect(document.querySelector("#workspace-feature-2 .is-panel-left")).toBeInTheDocument();
    expect(document.querySelector("#workspace-feature-2 .is-panel-right")).toBeInTheDocument();
  });

  it("shows validation feedback when generating a real photo without source files", async () => {
    render(<ImageStudioPage />);

    fireEvent.click(screen.getByRole("button", { name: /Generate Real Photo/i }));
    fireEvent.click(screen.getByRole("button", { name: "GENERATE REAL PHOTO" }));

    expect(await screen.findByText(/Failed: Source photo 파일을 선택하세요./)).toBeInTheDocument();
  });

  it("routes each image workspace and responds to browser history changes", async () => {
    render(<ImageStudioPage />);

    fireEvent.click(screen.getByRole("button", { name: "Generate Real Photo" }));
    expect(window.location.pathname).toBe("/app/image-studio/generate-real-photo");

    window.history.pushState({}, "", "/app/image-studio");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(window.location.pathname).toBe("/app/image-studio");
    await waitFor(() => expect(document.getElementById("menu-screen")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Edit Image" }));
    expect(window.location.pathname).toBe("/app/image-studio/edit-image");

    fireEvent.click(screen.getByRole("button", { name: /Back/i }));
    fireEvent.click(screen.getByRole("button", { name: "Decorate Image" }));
    expect(window.location.pathname).toBe("/app/image-studio/decorate-image");
  });

  it("opens edit and decorate workspaces with their expected controls", () => {
    render(<ImageStudioPage />);

    fireEvent.click(screen.getByRole("button", { name: /Edit Image/i }));
    expect(screen.getByText("Edit Mask")).toBeInTheDocument();
    expect(screen.getByText("Upload Reference Image")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Back/i }));
    fireEvent.click(screen.getByRole("button", { name: /Decorate Image/i }));
    expect(screen.getByText("Decorate Instructions")).toBeInTheDocument();
    expect(screen.queryByText("Edit Mask")).not.toBeInTheDocument();
  });
});
