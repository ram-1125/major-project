import { useEffect, useMemo, useRef, useState } from "react";
import { Maximize2, X } from "lucide-react";

export type LiveState = "live" | "paused" | "stale" | "offline";

export type InteractiveSeries<T> = {
  label: string;
  color: string;
  value: (item: T) => number | null;
};

type Viewport = { start: number; end: number };

type Props<T extends { timestamp_utc: string }> = {
  title: string;
  data: T[];
  series: InteractiveSeries<T>[];
  fixedMaximum?: number;
  axisFormatter: (value: number) => string;
  expandable?: boolean;
  liveState?: LiveState;
  tooltipDetails?: (item: T) => Array<{ label: string; value: string }>;
};

const MAX_RENDERED_POINTS = 1_200;

function clampViewport(view: Viewport): Viewport {
  const width = Math.min(1, Math.max(0.02, view.end - view.start));
  const start = Math.min(1 - width, Math.max(0, view.start));
  return { start, end: start + width };
}

/** Keep endpoints and bucket extrema, so long-range spikes are not averaged away. */
export function downsamplePreservingExtremes<T>(
  data: T[],
  series: InteractiveSeries<T>[],
  maximum = MAX_RENDERED_POINTS,
): T[] {
  if (data.length <= maximum) return data;
  const bucketSize = Math.max(1, Math.ceil(data.length / (maximum / 2)));
  const indexes = new Set<number>([0, data.length - 1]);
  for (let start = 0; start < data.length; start += bucketSize) {
    const end = Math.min(data.length, start + bucketSize);
    for (const definition of series) {
      let minimum: { index: number; value: number } | null = null;
      let maximumValue: { index: number; value: number } | null = null;
      for (let index = start; index < end; index += 1) {
        const value = definition.value(data[index]);
        if (value === null || !Number.isFinite(value)) continue;
        if (minimum === null || value < minimum.value) minimum = { index, value };
        if (maximumValue === null || value > maximumValue.value) {
          maximumValue = { index, value };
        }
      }
      if (minimum) indexes.add(minimum.index);
      if (maximumValue) indexes.add(maximumValue.index);
    }
  }
  const ordered = [...indexes].sort((left, right) => left - right);
  if (ordered.length <= maximum) return ordered.map((index) => data[index]);

  // Always retain endpoints and the global minimum/maximum for every series.
  // Fill remaining slots evenly from the per-bucket extrema candidates.
  const selected = new Set<number>([0, data.length - 1]);
  for (const definition of series) {
    const available = data
      .map((item, index) => ({ index, value: definition.value(item) }))
      .filter((item): item is { index: number; value: number } => item.value !== null);
    if (available.length > 0) {
      selected.add(available.reduce((left, right) => left.value <= right.value ? left : right).index);
      selected.add(available.reduce((left, right) => left.value >= right.value ? left : right).index);
    }
  }
  const optional = ordered.filter((index) => !selected.has(index));
  const remaining = Math.max(0, maximum - selected.size);
  for (let slot = 0; slot < remaining && optional.length > 0; slot += 1) {
    const position = remaining === 1
      ? Math.floor(optional.length / 2)
      : Math.round((slot / (remaining - 1)) * (optional.length - 1));
    selected.add(optional[position]);
  }
  return [...selected]
    .sort((left, right) => left - right)
    .map((index) => data[index]);
}

function exactLocalTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function InteractiveLineChart<T extends { timestamp_utc: string }>({
  title,
  data,
  series,
  fixedMaximum,
  axisFormatter,
  expandable = false,
  liveState = "offline",
  tooltipDetails,
}: Props<T>) {
  const [expanded, setExpanded] = useState(false);
  const [viewport, setViewport] = useState<Viewport>({ start: 0, end: 1 });
  const [followLive, setFollowLive] = useState(true);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null);
  const drag = useRef<{ x: number; viewport: Viewport; selecting: boolean } | null>(null);
  const pointers = useRef(new Map<number, number>());
  const pinchDistance = useRef<number | null>(null);
  const expandButtonRef = useRef<HTMLButtonElement | null>(null);
  const modalRef = useRef<HTMLDivElement | null>(null);
  const wasExpanded = useRef(false);

  useEffect(() => {
    if (!expanded || !followLive) return;
    setViewport((current) => {
      const width = current.end - current.start;
      return { start: Math.max(0, 1 - width), end: 1 };
    });
  }, [data.length, expanded, followLive]);

  useEffect(() => {
    if (!expanded) setHoverIndex(null);
  }, [expanded]);

  useEffect(() => {
    if (expanded) {
      wasExpanded.current = true;
      window.requestAnimationFrame(() => modalRef.current?.focus());
      return;
    }
    if (wasExpanded.current) {
      wasExpanded.current = false;
      expandButtonRef.current?.focus();
    }
  }, [expanded]);

  const selectedData = useMemo(() => {
    if (data.length === 0) return [];
    const start = Math.floor(viewport.start * Math.max(0, data.length - 1));
    const end = Math.max(start + 1, Math.ceil(viewport.end * data.length));
    return downsamplePreservingExtremes(data.slice(start, end), series);
  }, [data, series, viewport]);

  const zoomAt = (fraction: number, factor: number) => {
    setFollowLive(false);
    setViewport((current) => {
      const width = Math.min(1, Math.max(0.02, (current.end - current.start) * factor));
      const anchor = current.start + fraction * (current.end - current.start);
      return clampViewport({
        start: anchor - fraction * width,
        end: anchor + (1 - fraction) * width,
      });
    });
  };

  const panBy = (amount: number) => {
    setFollowLive(false);
    setViewport((current) => {
      const width = current.end - current.start;
      return clampViewport({
        start: current.start + amount * width,
        end: current.end + amount * width,
      });
    });
  };

  const renderChart = (large: boolean) => {
    const width = large ? 1_180 : 680;
    const height = large ? 520 : 230;
    const left = large ? 72 : 56;
    const right = 20;
    const top = 22;
    const bottom = large ? 52 : 38;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const values = series.flatMap((item) =>
      selectedData.map(item.value).filter((value): value is number => value !== null),
    );
    const validIndexes = selectedData
      .map((item, index) => ({
        index,
        available: series.some((definition) => definition.value(item) !== null),
      }))
      .filter((item) => item.available)
      .map((item) => item.index);
    const maximum = fixedMaximum ?? Math.max(1, ...values.map((value) => value * 1.1));
    const xFor = (index: number) => left + (
      selectedData.length <= 1
        ? plotWidth / 2
        : (index / (selectedData.length - 1)) * plotWidth
    );
    const yFor = (value: number) => top + plotHeight
      - (Math.min(Math.max(value, 0), maximum) / maximum) * plotHeight;
    const pathsFor = (definition: InteractiveSeries<T>) => {
      const paths: string[] = [];
      let current = "";
      selectedData.forEach((item, index) => {
        const value = definition.value(item);
        if (value === null) {
          if (current) paths.push(current);
          current = "";
          return;
        }
        const point = `${xFor(index).toFixed(1)},${yFor(value).toFixed(1)}`;
        current += current ? ` L ${point}` : `M ${point}`;
      });
      if (current) paths.push(current);
      return paths;
    };
    const latestIndex = selectedData.length - 1;
    const latestVisible = viewport.end > 0.995 && latestIndex >= 0;
    const activeHover = hoverIndex === null
      ? null
      : Math.min(latestIndex, Math.max(0, hoverIndex));
    const pointerFraction = (clientX: number, target: SVGSVGElement) => {
      const bounds = target.getBoundingClientRect();
      const safeClientX = Number.isFinite(clientX) ? clientX : bounds.left;
      return Math.min(1, Math.max(0, (safeClientX - bounds.left) / Math.max(1, bounds.width)));
    };

    return values.length === 0 ? (
      <div className="chart-empty">
        <strong>No valid readings</strong>
        <span>No evaluated measurement is available in the selected range. Missing values are not shown as zero.</span>
      </div>
    ) : (
      <div className="interactive-chart__plot">
        <svg
          className={`line-chart${large ? " line-chart--expanded" : ""}`}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${title} history`}
          tabIndex={large ? 0 : undefined}
          onWheel={large ? (event) => {
            event.preventDefault();
            zoomAt(pointerFraction(event.clientX, event.currentTarget), event.deltaY > 0 ? 1.18 : 0.82);
          } : undefined}
          onPointerDown={large ? (event) => {
            event.currentTarget.setPointerCapture?.(event.pointerId);
            const currentX = Number.isFinite(event.clientX) ? event.clientX : 0;
            pointers.current.set(event.pointerId, currentX);
            if (pointers.current.size === 2) {
              const points = [...pointers.current.values()];
              pinchDistance.current = Math.abs(points[0] - points[1]);
              return;
            }
            drag.current = {
              x: currentX,
              viewport,
              selecting: event.shiftKey,
            };
            if (event.shiftKey) {
              const fraction = pointerFraction(currentX, event.currentTarget);
              setSelection({ start: fraction, end: fraction });
            }
          } : undefined}
          onPointerMove={(event) => {
            const fraction = pointerFraction(event.clientX, event.currentTarget);
            setHoverIndex(Math.round(fraction * Math.max(0, latestIndex)));
            if (!large) return;
            const currentX = Number.isFinite(event.clientX) ? event.clientX : 0;
            pointers.current.set(event.pointerId, currentX);
            if (pointers.current.size === 2) {
              const points = [...pointers.current.values()];
              const distance = Math.abs(points[0] - points[1]);
              if (pinchDistance.current && distance > 0) {
                zoomAt(0.5, pinchDistance.current / distance);
              }
              pinchDistance.current = distance;
              return;
            }
            if (!drag.current) return;
            const bounds = event.currentTarget.getBoundingClientRect();
            const delta = (currentX - drag.current.x) / Math.max(1, bounds.width);
            if (drag.current.selecting) {
              setSelection((current) => current
                ? { ...current, end: fraction }
                : { start: fraction, end: fraction });
            } else {
              setFollowLive(false);
              const original = drag.current.viewport;
              const viewWidth = original.end - original.start;
              setViewport(clampViewport({
                start: original.start - delta * viewWidth,
                end: original.end - delta * viewWidth,
              }));
            }
          }}
          onPointerUp={large ? (event) => {
            pointers.current.delete(event.pointerId);
            pinchDistance.current = null;
            if (drag.current?.selecting && selection) {
              const lower = Math.min(selection.start, selection.end);
              const upper = Math.max(selection.start, selection.end);
              if (upper - lower >= 0.03) {
                const widthBefore = viewport.end - viewport.start;
                setViewport(clampViewport({
                  start: viewport.start + lower * widthBefore,
                  end: viewport.start + upper * widthBefore,
                }));
                setFollowLive(false);
              }
            }
            drag.current = null;
            setSelection(null);
          } : undefined}
          onPointerCancel={large ? (event) => {
            pointers.current.delete(event.pointerId);
            drag.current = null;
            pinchDistance.current = null;
            setSelection(null);
          } : undefined}
          onPointerLeave={() => {
            if (!drag.current) setHoverIndex(null);
          }}
        >
          {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
            const y = top + plotHeight - fraction * plotHeight;
            return (
              <g key={fraction}>
                <line x1={left} x2={width - right} y1={y} y2={y} className="chart-gridline" />
                <text x={left - 8} y={y + 4} className="chart-axis" textAnchor="end">
                  {axisFormatter(maximum * fraction)}
                </text>
              </g>
            );
          })}
          {series.flatMap((item) => pathsFor(item).map((path, index) => (
            <path
              key={`${item.label}-${index}`}
              className="chart-series-path"
              d={path}
              fill="none"
              stroke={item.color}
              strokeWidth={large ? 2.2 : 2.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )))}
          {validIndexes.length === 1 && series.map((item) => {
            const index = validIndexes[0];
            const value = item.value(selectedData[index]);
            return value === null ? null : (
              <circle
                key={`single-${item.label}`}
                className="chart-single-point"
                cx={xFor(index)}
                cy={yFor(value)}
                r="4"
                fill={item.color}
              />
            );
          })}
          {activeHover !== null && (
            <line
              className="chart-crosshair"
              x1={xFor(activeHover)}
              x2={xFor(activeHover)}
              y1={top}
              y2={top + plotHeight}
            />
          )}
          {selection && (
            <rect
              className="chart-selection"
              x={left + Math.min(selection.start, selection.end) * plotWidth}
              y={top}
              width={Math.abs(selection.end - selection.start) * plotWidth}
              height={plotHeight}
            />
          )}
          {latestVisible && liveState === "live" && series.map((item) => {
            const value = item.value(selectedData[latestIndex]);
            return value === null ? null : (
              <circle
                key={`live-${item.label}`}
                className="chart-live-point"
                cx={xFor(latestIndex)}
                cy={yFor(value)}
                r="4"
                fill={item.color}
              />
            );
          })}
          <text x={left} y={height - 10} className="chart-axis">
            {new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date(selectedData[0].timestamp_utc))}
          </text>
          <text x={width - right} y={height - 10} className="chart-axis" textAnchor="end">
            {new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(new Date(selectedData[latestIndex].timestamp_utc))}
          </text>
        </svg>
        {activeHover !== null && selectedData[activeHover] && (
          <div className="chart-tooltip" role="status">
            <strong>{exactLocalTime(selectedData[activeHover].timestamp_utc)}</strong>
            {series.map((item) => {
              const value = item.value(selectedData[activeHover]);
              return (
                <span key={item.label}>
                  {item.label}: {value === null ? "Unavailable" : axisFormatter(value)}
                </span>
              );
            })}
            {tooltipDetails?.(selectedData[activeHover]).map((item) => (
              <span key={item.label}>{item.label}: {item.value}</span>
            ))}
          </div>
        )}
        {validIndexes.length === 1 && (
          <p className="chart-single-reading" role="status">
            One valid reading is available. Another reading is required to draw a trend.
          </p>
        )}
      </div>
    );
  };

  const stateLabel = liveState === "live" ? "Live"
    : liveState === "paused" ? "Paused"
      : liveState === "stale" ? "Stale"
      : "Offline";

  return (
    <article className="chart-card">
      <div className="chart-heading">
        <h3>{title}</h3>
        <div className="chart-heading__actions">
          <span className={`chart-live-status chart-live-status--${liveState}`}>
            {stateLabel}
          </span>
          {expandable && (
            <button ref={expandButtonRef} type="button" className="chart-expand" onClick={() => setExpanded(true)}>
              <Maximize2 aria-hidden="true" size={15} /> Expand
            </button>
          )}
        </div>
      </div>
      <div className="chart-legend">
        {series.map((item) => (
          <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>
        ))}
      </div>
      {data.length === 0 ? <div className="chart-empty">No data in the selected range</div> : renderChart(false)}

      {expanded && (
        <div
          ref={modalRef}
          className="chart-modal"
          role="dialog"
          aria-modal="true"
          aria-label={`${title} expanded chart`}
          tabIndex={-1}
          onKeyDown={(event) => {
            if (event.key === "Escape") setExpanded(false);
          }}
        >
          <div className="chart-modal__panel">
            <header>
              <div>
                <h2>{title}</h2>
                <p>Wheel or pinch to zoom. Drag to pan; Shift+drag selects a range.</p>
              </div>
              <button type="button" onClick={() => setExpanded(false)} aria-label={`Close ${title} expanded chart`}><X aria-hidden="true" size={16} /> Close</button>
            </header>
            <div className="chart-modal__controls" aria-label={`${title} timeline controls`}>
              <button type="button" onClick={() => panBy(-0.35)}>Pan earlier</button>
              <button type="button" onClick={() => panBy(0.35)}>Pan later</button>
              <button type="button" onClick={() => { setViewport({ start: 0, end: 1 }); setFollowLive(false); }}>Reset Zoom</button>
              <button type="button" onClick={() => { setViewport({ start: 0, end: 1 }); setFollowLive(false); }}>Fit All</button>
              <button type="button" onClick={() => {
                const width = viewport.end - viewport.start;
                setViewport({ start: Math.max(0, 1 - width), end: 1 });
                setFollowLive(true);
              }}>Return to Live</button>
              <span className={`chart-live-status chart-live-status--${liveState}`}>{stateLabel}</span>
            </div>
            {renderChart(true)}
            <label className="chart-timeline">
              <span>Scrollable historical timeline</span>
              <input
                aria-label={`${title} historical timeline`}
                type="range"
                min="0"
                max="1000"
                value={Math.round(viewport.start * 1000)}
                onChange={(event) => {
                  const width = viewport.end - viewport.start;
                  const start = Math.min(1 - width, Number(event.target.value) / 1000);
                  setViewport({ start, end: start + width });
                  setFollowLive(false);
                }}
              />
            </label>
            <p className="chart-resolution-note">
              Showing {selectedData.length.toLocaleString()} points from {data.length.toLocaleString()} bounded records. Bucket extrema preserve visible spikes.
            </p>
          </div>
        </div>
      )}
    </article>
  );
}
