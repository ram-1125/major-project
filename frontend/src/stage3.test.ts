// @ts-expect-error Vitest executes this test in Node; the browser bundle has no Node dependency.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/stage3.css", "utf8");

describe("deployment page layout", () => {
  it("uses the document as the primary vertical scroller", () => {
    expect(css).toMatch(/body,\s*\n#root,\s*\n\.app-shell[\s\S]*min-height:\s*100vh/);
    expect(css).toMatch(/\.app-content[\s\S]*height:\s*auto[\s\S]*overflow-y:\s*visible/);
    expect(css).toMatch(/\.topbar\s*\{[\s\S]*position:\s*sticky/);
    expect(css).toMatch(/\.sidebar\s*\{[\s\S]*position:\s*sticky/);
  });

  it("retains responsive and reduced-motion safeguards", () => {
    expect(css).toContain("@media (max-width: 900px)");
    expect(css).toContain("@media (max-width: 520px)");
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).toContain("overflow-x: clip");
  });
});
