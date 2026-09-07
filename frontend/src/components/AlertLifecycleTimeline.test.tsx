import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AlertLifecycleTimeline } from "./AlertLifecycleTimeline";

afterEach(cleanup);

describe("AlertLifecycleTimeline", () => {
  it("renders only timestamps backed by recorded lifecycle evidence", () => {
    render(<AlertLifecycleTimeline
      firstObservedUtc="2026-09-03T10:00:00Z"
      occurrences={[{ id: 1, observed_at_utc: "2026-09-03T10:00:00Z", severity: "warning" }]}
      transitions={[{ id: 2, transition_timestamp_utc: "2026-09-03T10:05:00Z", previous_state: "open", new_state: "acknowledged", transition_type: "acknowledged" }]}
      deliveries={[{ id: 3, attempted_at_utc: "2026-09-03T10:01:00Z", delivery_status: "delivered", notification_type: "activation", severity: "warning" }]}
      outcome={null}
      formatTimestamp={(value) => value}
    />);
    expect(screen.getByText("Evidence observed")).toBeVisible();
    expect(screen.getByText("Notification delivered")).toBeVisible();
    expect(screen.getByText("Acknowledged")).toBeVisible();
    expect(screen.queryByText("Resolved")).not.toBeInTheDocument();
    expect(screen.queryByText("Outcome recorded")).not.toBeInTheDocument();
  });
});
