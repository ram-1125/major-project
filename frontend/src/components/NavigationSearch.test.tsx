import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NavigationSearchDialog } from "./NavigationSearch";

afterEach(() => { cleanup(); window.location.hash = ""; });

describe("NavigationSearchDialog", () => {
  it("groups and highlights local application results", () => {
    render(<NavigationSearchDialog open initialQuery="video editing" onClose={() => undefined} />);
    expect(screen.getByRole("dialog", { name: "Search SmartOps navigation" })).toBeVisible();
    const result = screen.getByRole("option", { name: /Video editing and rendering/ });
    expect(result).toHaveAttribute("href", "#/pc-quality-check?profile=video_editing_rendering");
    expect(result.querySelector("mark")).toHaveTextContent(/video editing/i);
    expect(screen.getByRole("region", { name: "PC Quality Profiles" })).toBeVisible();
  });

  it("supports Arrow keys, Enter and Escape", () => {
    const onClose = vi.fn();
    render(<NavigationSearchDialog open initialQuery="" onClose={onClose} />);
    const input = screen.getByRole("textbox", { name: "Search SmartOps navigation" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe("#/live-monitoring");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("shows a clear no-results state", () => {
    render(<NavigationSearchDialog open initialQuery="definitely-not-a-smartops-feature" onClose={() => undefined} />);
    expect(screen.getByText("No results")).toBeVisible();
  });
});
