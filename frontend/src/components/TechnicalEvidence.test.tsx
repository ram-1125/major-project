import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TechnicalEvidence } from "./TechnicalEvidence";

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.location.hash = ""; });

describe("TechnicalEvidence", () => {
  it("provides eight read-only tabs and bounded server pagination", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ dataset: "monitoring", items: [{ id: 1, device_id: "local" }], total: 30, limit: 25, offset: 0, read_only: true }),
    } as Response);
    render(<TechnicalEvidence apiBaseUrl="http://127.0.0.1:8000" />);
    expect(screen.getAllByRole("tab")).toHaveLength(8);
    expect(screen.getByText(/Audit Records remain in Settings/)).toBeVisible();
    await screen.findByText("local");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining("offset=25"), expect.anything()));
  });

  it("opens an exact deep-linked dataset and search without exposing personal content", async () => {
    window.location.hash = "#/technical-evidence?dataset=alert-evidence&search=42";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => ({ items: [], total: 0 }) } as Response);
    render(<TechnicalEvidence apiBaseUrl="http://127.0.0.1:8000" />);
    expect(screen.getByRole("tab", { name: "Alerts" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByDisplayValue("42")).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/alert-evidence?"), expect.anything()));
  });

  it("keeps an alert context across related record types and clears it for another tab", async () => {
    window.location.hash = "#/technical-evidence?dataset=alerts&contextId=42";
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({ ok: true, json: async () => ({ items: [], total: 0 }) } as Response);
    render(<TechnicalEvidence apiBaseUrl="http://127.0.0.1:8000" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/alerts\?.*context_id=42/), expect.anything()));
    fireEvent.change(screen.getByLabelText("Record type"), { target: { value: "alert-evidence" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/alert-evidence\?.*context_id=42/), expect.anything()));
    fireEvent.click(screen.getByRole("tab", { name: "Monitoring" }));
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith(expect.not.stringContaining("context_id="), expect.anything()));
  });
});
