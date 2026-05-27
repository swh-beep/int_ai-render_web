import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { SideNav } from "../components/SideNav";

vi.mock("../pages/MarketingPage", () => ({
  MarketingPage: () => <main><h1>Marketing Studio Route</h1></main>,
}));

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

describe("studio app marketing route", () => {
  it("renders the Marketing app at /marketing", () => {
    renderAt("/marketing");

    expect(screen.getByRole("heading", { name: "Marketing Studio Route" })).toBeInTheDocument();
  });

  it("links navigation back to the public static routes", () => {
    render(<SideNav activePath="/marketing" />);

    expect(screen.getByRole("link", { name: /MAIN/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /IMAGE STUDIO/i })).toHaveAttribute("href", "/image-studio");
    expect(screen.getByRole("link", { name: /VIDEO STUDIO/i })).toHaveAttribute("href", "/video-studio");
    expect(screen.getByRole("link", { name: /MARKETING/i })).toHaveAttribute("href", "/marketing");
  });
});
