import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  InteractiveLineChart,
  downsamplePreservingExtremes,
} from "./InteractiveLineChart";

type Point = { timestamp_utc: string; value: number | null };

const points: Point[] = Array.from({ length: 40 }, (_, index) => ({
  timestamp_utc: new Date(Date.UTC(2026, 7, 17, 10, 0, index * 30)).toISOString(),
  value: index === 20 ? 99 : index,
}));

const series = [{
  label: "CPU",
  color: "#2f6fad",
  value: (point: Point) => point.value,
}];

afterEach(cleanup);

describe("InteractiveLineChart", () => {
  it("expands, zooms, pans, selects, returns live and closes", () => {
    render(
      <InteractiveLineChart
        title="CPU utilization"
        data={points}
        series={series}
        axisFormatter={(value) => `${value.toFixed(1)}%`}
        expandable
        liveState="live"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    const dialog = screen.getByRole("dialog", { name: "CPU utilization expanded chart" });
    const chart = screen.getAllByRole("img", { name: "CPU utilization history" })[1];
    fireEvent.wheel(chart, { deltaY: -100, clientX: 300 });
    fireEvent.click(screen.getByRole("button", { name: "Pan earlier" }));
    fireEvent.pointerDown(chart, { pointerId: 1, clientX: 100, shiftKey: true });
    fireEvent.pointerMove(chart, { pointerId: 1, clientX: 500, shiftKey: true });
    fireEvent.pointerUp(chart, { pointerId: 1, clientX: 500, shiftKey: true });
    fireEvent.click(screen.getByRole("button", { name: "Reset Zoom" }));
    fireEvent.click(screen.getByRole("button", { name: "Fit All" }));
    fireEvent.click(screen.getByRole("button", { name: "Return to Live" }));
    expect(dialog).toHaveTextContent("Showing 40 points from 40 bounded records");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand" })).toHaveFocus();
  });

  it("shows an exact crosshair tooltip and genuine live-state marker", () => {
    const { container } = render(
      <InteractiveLineChart
        title="CPU utilization"
        data={points}
        series={series}
        axisFormatter={(value) => `${value.toFixed(1)}%`}
        liveState="live"
        tooltipDetails={(point) => [{ label: "Window state", value: point.value === null ? "Not evaluated" : "Established" }]}
      />,
    );
    fireEvent.pointerMove(screen.getByRole("img"), { clientX: 0 });
    expect(screen.getByRole("status")).toHaveTextContent("CPU: 0.0%");
    expect(screen.getByRole("status")).toHaveTextContent("Window state: Established");
    expect(container.querySelector(".chart-crosshair")).not.toBeNull();
    expect(container.querySelector(".chart-live-point")).not.toBeNull();
    expect(screen.getByText("Live")).toBeVisible();
  });

  it("does not pulse when paused, stale or offline", () => {
    const { container, rerender } = render(
      <InteractiveLineChart
        title="CPU utilization"
        data={points}
        series={series}
        axisFormatter={(value) => `${value.toFixed(1)}%`}
        liveState="paused"
      />,
    );
    expect(container.querySelector(".chart-live-point")).toBeNull();
    expect(screen.getByText("Paused")).toBeVisible();
    rerender(
      <InteractiveLineChart
        title="CPU utilization"
        data={points}
        series={series}
        axisFormatter={(value) => `${value.toFixed(1)}%`}
        liveState="offline"
      />,
    );
    expect(container.querySelector(".chart-live-point")).toBeNull();
    expect(screen.getByText("Offline")).toBeVisible();
  });

  it("bounds All Data rendering while preserving a narrow spike", () => {
    const longHistory = Array.from({ length: 10_000 }, (_, index) => ({
      timestamp_utc: new Date(index * 30_000).toISOString(),
      value: index === 5_001 ? 10_000 : 1,
    }));
    const sampled = downsamplePreservingExtremes(longHistory, series, 1_200);
    expect(sampled.length).toBeLessThanOrEqual(1_200);
    expect(sampled.some((point) => point.value === 10_000)).toBe(true);
  });

  it("distinguishes zero, one, and two valid readings without fabricating zero", () => {
    const { rerender, container } = render(
      <InteractiveLineChart
        title="CPU utilization"
        data={[{ timestamp_utc: points[0].timestamp_utc, value: null }]}
        series={series}
        axisFormatter={(value) => `${value.toFixed(1)}%`}
      />,
    );
    expect(screen.getByText("No valid readings")).toBeVisible();
    expect(screen.getByText(/Missing values are not shown as zero/)).toBeVisible();

    rerender(<InteractiveLineChart
      title="CPU utilization"
      data={[points[0]]}
      series={series}
      axisFormatter={(value) => `${value.toFixed(1)}%`}
    />);
    expect(screen.getByText(/Another reading is required to draw a trend/)).toBeVisible();
    expect(container.querySelector(".chart-single-point")).not.toBeNull();

    rerender(<InteractiveLineChart
      title="CPU utilization"
      data={[points[0], points[1]]}
      series={series}
      axisFormatter={(value) => `${value.toFixed(1)}%`}
    />);
    expect(screen.queryByText(/Another reading is required/)).not.toBeInTheDocument();
    expect(container.querySelector(".chart-series-path[d*=' L ']")).not.toBeNull();
  });

  it("keeps missing measurements as separate path segments and reports stale state", () => {
    const { container } = render(
      <InteractiveLineChart
        title="CPU utilization"
        data={[points[0], { ...points[1], value: null }, points[2]]}
        series={series}
        axisFormatter={(value) => `${value.toFixed(1)}%`}
        liveState="stale"
      />,
    );
    expect(screen.getByText("Stale")).toBeVisible();
    expect(container.querySelectorAll(".chart-series-path")).toHaveLength(2);
    expect(container.querySelector(".chart-live-point")).toBeNull();
  });
});
