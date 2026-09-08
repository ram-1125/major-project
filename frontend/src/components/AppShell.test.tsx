import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AppShell } from "./AppShell";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.location.hash = "";
});

function renderShell(
  route: "overview" | "live-monitoring" | "settings" = "overview",
) {
  return render(
    <AppShell
      activeRoute={route}
      connection={<span>Database connected</span>}
      refreshAction={<button type="button">Refresh</button>}
    >
      <h1>Page content</h1>
    </AppShell>,
  );
}

describe("AppShell", () => {
  it("provides semantic keyboard-accessible navigation", () => {
    renderShell();
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    expect(
      screen.getByRole("navigation", { name: "Application utilities" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /Research & Validation/ })).toHaveTextContent(
      "Optional",
    );
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "#/settings",
    );
  });

  it("exposes an accessible compact-menu toggle", () => {
    renderShell();
    const toggle = screen.getByRole("button", { name: "Open navigation menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("link", { name: "Settings" })).toBeVisible();
    expect(
      screen
        .getAllByRole("button", { name: "Close navigation menu" })
        .find((item) => item.hasAttribute("aria-controls")),
    ).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Open navigation menu" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("assigns scrolling to content while ribbon and navigation remain fixed", () => {
    const { container } = renderShell("live-monitoring");
    expect(container.querySelector("[data-fixed-layout='true']")).not.toBeNull();
    expect(container.querySelector("[data-fixed-ribbon='true']")).not.toBeNull();
    expect(container.querySelector("[data-fixed-navigation='true']")).not.toBeNull();
    expect(container.querySelector("main[data-scroll-owner='true']")).not.toBeNull();
  });

  it("groups routes and exposes meaningful decorative icons", () => {
    const { container } = renderShell("live-monitoring");
    expect(screen.getByText("Monitoring")).toBeVisible();
    expect(screen.getByText("Insights")).toBeVisible();
    expect(screen.getByText("Application")).toBeVisible();
    const active = screen.getByRole("link", { name: "Live Monitoring" });
    expect(active).toHaveAttribute("aria-current", "page");
    expect(active.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelectorAll(".nav-link svg")).toHaveLength(9);
  });

  it("persists desktop sidebar collapse without hiding route access", () => {
    const { container, unmount } = renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(container.querySelector(".app-shell")).toHaveClass("app-shell--collapsed");
    expect(window.localStorage.getItem("smartops.sidebar.collapsed.v1")).toBe("true");
    expect(screen.getByRole("link", { name: "PC Quality Check" })).toHaveAttribute("href", "#/pc-quality-check");
    unmount();
    const next = renderShell();
    expect(next.container.querySelector(".app-shell")).toHaveClass("app-shell--collapsed");
  });

  it("opens navigation search with Ctrl+K and closes it with Escape", () => {
    renderShell("settings");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "Search SmartOps navigation" })).toBeVisible();
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Search SmartOps navigation" }), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Search SmartOps navigation" })).not.toBeInTheDocument();
  });

  it("shows genuine connection content and current page context in the top bar", () => {
    renderShell("settings");
    expect(screen.getByText("Database connected")).toBeVisible();
    expect(screen.getAllByText("Application preferences, notifications and personal baseline")[0]).toBeVisible();
    expect(screen.getByRole("link", { name: "Open notification settings" })).toHaveAttribute("href", "#/settings?section=notifications");
  });
});
