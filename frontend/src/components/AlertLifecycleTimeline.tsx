import { Bell, CheckCircle2, CircleDot, Eye, History } from "lucide-react";

type Transition = {
  id: number;
  transition_timestamp_utc: string;
  previous_state: string | null;
  new_state: string;
  transition_type: string;
};

type Occurrence = {
  id: number;
  observed_at_utc: string;
  severity: string;
};

type Delivery = {
  id: number;
  attempted_at_utc: string;
  delivery_status: "attempting" | "delivered" | "failed";
  notification_type: "activation" | "escalation";
  severity: string;
};

type Outcome = {
  new_outcome: string;
  event_timestamp_utc: string;
} | null;

type TimelineItem = {
  key: string;
  timestamp: string;
  label: string;
  detail: string;
  icon: typeof CircleDot;
  tone: string;
};

function words(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function AlertLifecycleTimeline({
  firstObservedUtc,
  transitions = [],
  occurrences = [],
  deliveries = [],
  outcome,
  formatTimestamp,
}: {
  firstObservedUtc: string;
  transitions?: Transition[];
  occurrences?: Occurrence[];
  deliveries?: Delivery[];
  outcome: Outcome;
  formatTimestamp: (value: string) => string;
}) {
  const items: TimelineItem[] = [];
  items.push(...occurrences.map((occurrence) => ({
    key: `evidence-${occurrence.id}`,
    timestamp: occurrence.observed_at_utc,
    label: "Evidence observed",
    detail: `${words(occurrence.severity)} evidence recorded`,
    icon: CircleDot,
    tone: "evidence",
  })));
  items.push(...deliveries.map((delivery) => ({
    key: `delivery-${delivery.id}`,
    timestamp: delivery.attempted_at_utc,
    label: `Notification ${delivery.delivery_status}`,
    detail: `${words(delivery.notification_type)} notification - ${words(delivery.severity)}`,
    icon: Bell,
    tone: delivery.delivery_status === "failed" ? "failed" : "notification",
  })));
  items.push(...transitions.map((transition) => ({
    key: `transition-${transition.id}`,
    timestamp: transition.transition_timestamp_utc,
    label: words(transition.transition_type),
    detail: transition.previous_state
      ? `${words(transition.previous_state)} to ${words(transition.new_state)}`
      : words(transition.new_state),
    icon: transition.new_state === "resolved" ? CheckCircle2 : Eye,
    tone: transition.new_state === "resolved" ? "resolved" : "transition",
  })));
  if (outcome) {
    items.push({
      key: `outcome-${outcome.event_timestamp_utc}`,
      timestamp: outcome.event_timestamp_utc,
      label: "Outcome recorded",
      detail: words(outcome.new_outcome),
      icon: History,
      tone: "outcome",
    });
  }
  items.sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));

  if (items.length === 0) {
    return (
      <p className="alert-lifecycle-empty">
        No detailed lifecycle events were stored for this legacy alert. Its
        recorded first-observed time is {formatTimestamp(firstObservedUtc)}.
      </p>
    );
  }

  return (
    <ol className="alert-lifecycle" aria-label="Recorded alert lifecycle">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <li className={`alert-lifecycle__item alert-lifecycle__item--${item.tone}`} key={item.key}>
            <span className="alert-lifecycle__icon" aria-hidden="true"><Icon size={16} /></span>
            <div>
              <strong>{item.label}</strong>
              <span>{item.detail}</span>
              <time dateTime={item.timestamp}>{formatTimestamp(item.timestamp)}</time>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
