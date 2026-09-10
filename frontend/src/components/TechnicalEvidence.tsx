import { ChevronLeft, ChevronRight, Database, Search } from "lucide-react";
import { useEffect, useState } from "react";

type Dataset = { key: string; label: string; description: string; sources: Array<[string, string]> };
type Page = { dataset: string; items: Array<Record<string, unknown>>; total: number; limit: number; offset: number; read_only: boolean };

const FALLBACK_DATASETS: Dataset[] = [
  { key: "monitoring", label: "Monitoring", description: "Raw telemetry, process tables, event records and Advanced Signal provenance", sources: [["monitoring", "Raw telemetry"], ["monitoring-processes", "Process snapshots"], ["advanced-signals", "Advanced Signals"], ["windows-events", "Windows events"]] },
  { key: "alerts", label: "Alerts", description: "Occurrences, evidence, transitions and notification delivery", sources: [["alerts", "Occurrences"], ["alert-evidence", "Evidence"], ["alert-transitions", "State transitions"], ["notification-deliveries", "Notification delivery"]] },
  { key: "root-causes", label: "Root Causes", description: "Ranked hypotheses and evidence relationships", sources: [["root-causes", "Ranked candidates"], ["root-cause-evidence", "Evidence relationships"]] },
  { key: "system-health", label: "System Health", description: "Assessments, components and reconstructable deductions", sources: [["system-health", "Assessments"], ["health-components", "Components"], ["health-deductions", "Deductions"]] },
  { key: "pc-quality", label: "PC Quality", description: "Assessments and contribution calculations", sources: [["pc-quality", "Assessments"], ["pc-quality-contributions", "Metric contributions"]] },
  { key: "personal-baseline", label: "Personal Baseline", description: "Versioned profile and membership provenance", sources: [["personal-baseline", "Profiles"], ["baseline-membership", "Membership audit"]] },
  { key: "analytical-records", label: "Analytical Records", description: "Five-minute analysis periods", sources: [["analytical-records", "Feature windows"]] },
  { key: "validation", label: "Validation", description: "Versioned calculations, inclusion decisions and incident-match audit", sources: [["validation-metrics", "Metric calculations"], ["validation-decisions", "Evidence decisions"], ["validation-matches", "Incident matching"]] },
];

function display(value: unknown): string {
  if (value === null || value === undefined) return "Not recorded";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function TechnicalEvidence({ apiBaseUrl }: { apiBaseUrl: string }) {
  const initialParameters = new URLSearchParams(window.location.hash.split("?", 2)[1] ?? "");
  const requestedDataset = initialParameters.get("dataset") ?? "monitoring";
  const initialTab = FALLBACK_DATASETS.find((item) => item.sources.some(([key]) => key === requestedDataset)) ?? FALLBACK_DATASETS[0];
  const [tab, setTab] = useState(initialTab.key);
  const [dataset, setDataset] = useState(initialTab.sources.some(([key]) => key === requestedDataset) ? requestedDataset : initialTab.sources[0][0]);
  const [search, setSearch] = useState(initialParameters.get("search") ?? "");
  const [contextId, setContextId] = useState(initialParameters.get("contextId"));
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const limit = 25;

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setState("loading");
      const parameters = new URLSearchParams({ limit: String(limit), offset: String(offset), sort: "newest" });
      if (search.trim()) parameters.set("search", search.trim());
      if (contextId && /^[1-9]\d*$/.test(contextId)) parameters.set("context_id", contextId);
      void fetch(`${apiBaseUrl}/api/technical-evidence/${dataset}?${parameters}`, { signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error("Technical evidence could not be loaded.");
          return response.json() as Promise<Page>;
        })
        .then((result) => { setPage(result); setState("ready"); })
        .catch((error: unknown) => {
          if (!(error instanceof DOMException && error.name === "AbortError")) setState("error");
        });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [apiBaseUrl, contextId, dataset, offset, search]);

  const selectTab = (key: string) => { const selected = FALLBACK_DATASETS.find((item) => item.key === key)!; setTab(key); setDataset(selected.sources[0][0]); setContextId(null); setOffset(0); setSearch(""); };
  const activeTab = FALLBACK_DATASETS.find((item) => item.key === tab)!;
  const items = Array.isArray(page?.items) ? page.items : [];
  const total = typeof page?.total === "number" ? page.total : items.length;
  const columns = Object.keys(items[0] ?? {}).slice(0, 7);
  return (
    <section className="technical-evidence-page" aria-label="Technical Evidence">
      <div className="technical-evidence-intro">
        <Database aria-hidden="true" />
        <div><h2>Technical Evidence</h2><p>Read-only, bounded access for guides, evaluators and advanced investigation. Audit Records remain in Settings.</p></div>
        <span>Read only</span>
      </div>
      <div className="technical-evidence-tabs" role="tablist" aria-label="Technical evidence datasets">
        {FALLBACK_DATASETS.map((item) => <button key={item.key} type="button" role="tab" aria-selected={tab === item.key} onClick={() => selectTab(item.key)}>{item.label}</button>)}
      </div>
      <div className="technical-evidence-toolbar">
        <label><span>Search this dataset</span><div><Search aria-hidden="true" /><input value={search} onChange={(event) => { setSearch(event.target.value); setOffset(0); }} /></div></label>
        {activeTab.sources.length > 1 && <label><span>Record type</span><select value={dataset} onChange={(event) => { setDataset(event.target.value); setOffset(0); }}>{activeTab.sources.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>}
        <p>{activeTab.description}</p>
      </div>
      {state === "loading" && <div className="empty-state" role="status">Loading bounded technical records…</div>}
      {state === "error" && <div className="error-message" role="alert">Technical evidence is temporarily unavailable. No records were changed.</div>}
      {state === "ready" && items.length === 0 && <div className="empty-state">No records match this dataset and search.</div>}
      {state === "ready" && page && items.length > 0 && <>
        <div className="table-wrap"><table><thead><tr>{columns.map((column) => <th key={column}>{column.replaceAll("_", " ")}</th>)}<th>Complete record</th></tr></thead><tbody>
          {items.map((record, index) => <tr key={String(record.id ?? index)}>{columns.map((column) => <td key={column}>{display(record[column])}</td>)}<td><details><summary>View exact stored fields</summary><pre>{JSON.stringify(record, null, 2)}</pre></details></td></tr>)}
        </tbody></table></div>
        <div className="pagination"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}><ChevronLeft aria-hidden="true" />Previous</button><span>{offset + 1}–{Math.min(offset + limit, total)} of {total.toLocaleString()}</span><button type="button" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>Next<ChevronRight aria-hidden="true" /></button></div>
      </>}
    </section>
  );
}
