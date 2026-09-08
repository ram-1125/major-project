// @ts-expect-error Vitest executes this test in Node; the application bundle
// itself has no Node runtime dependency.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function luminance(hex: string): number {
  const channels = hex.match(/[0-9a-f]{2}/gi)!.map((channel) => parseInt(channel, 16) / 255)
    .map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(first: string, second: string): number {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe("Stage 1 design system", () => {
  it.each([
    ["#102a43", "#f4f8fc"], ["#566f86", "#ffffff"], ["#ffffff", "#0b304b"],
    ["#ffffff", "#0f78b5"], ["#ffffff", "#12845a"], ["#ffffff", "#9c6300"],
    ["#ffffff", "#c64f18"], ["#ffffff", "#c93636"],
  ])("meets WCAG AA normal-text contrast for %s on %s", (foreground, background) => {
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
  });

  it("contains responsive, focus, overflow and reduced-motion safeguards", () => {
    const css = readFileSync("src/stage1.css", "utf8");
    expect(css).toContain("@media (max-width: 900px)");
    expect(css).toContain("@media (max-width: 480px)");
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).toContain(":focus-visible");
    expect(css).toContain("minmax(0, 1fr)");
  });

  it("limits pipeline motion to genuine processing and disables it for reduced motion", () => {
    const css = readFileSync("src/overview.css", "utf8");
    expect(css).toContain(".overview-pipeline-stage--processing .overview-pipeline-ring");
    expect(css).toContain("animation: overview-pipeline-pulse");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("animation: none");
  });

  it("keeps Gaming/3D red and Background Activity grey with distinct fallbacks", () => {
    const css = readFileSync("src/overview.css", "utf8");
    expect(css).toContain(".overview-heat--gaming { background: #d94343; }");
    expect(css).toContain(".overview-heat--background { background: #94a5b4; }");
    expect(css).toContain(".overview-heat--unknown { background: #637d91; }");
    expect(css).toContain(".overview-heat--missing");
    expect(css).not.toContain(".overview-heat--gaming { background: #94a5b4; }");
  });
});
