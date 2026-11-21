import { useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { CaseDetail, ModeBadge } from "./CaseDetail";
import { CaseTable } from "./CaseTable";
import { loadComparison, loadIndex, sourceLabel } from "./data";
import type { Comparison, ComparisonEntry } from "./data";
import { Icon } from "./Icons";
import { Overview, VerdictBadge } from "./Overview";
import { caseChange, formatPoints } from "./presentation";

type Resource<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; message: string };

function useResource<T, Key>(key: Key, load: (key: Key, signal: AbortSignal) => Promise<T>) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ key: Key; revision: number; resource: Resource<T> }>();
  useEffect(() => {
    const controller = new AbortController();
    load(key, controller.signal).then((data) => {
      if (!controller.signal.aborted) setResult({ key, revision, resource: { status: "ready", data } });
    }).catch((error: unknown) => {
      const message = error instanceof TypeError ? "The artifact source is unavailable." :
        error instanceof Error ? error.message : "The artifact could not be loaded.";
      if (!controller.signal.aborted) setResult({ key, revision, resource: { status: "error", message } });
    });
    return () => controller.abort();
  }, [key, load, revision]);

  const resource: Resource<T> = result?.key === key && result.revision === revision ? result.resource : { status: "loading" };
  return [resource, () => setRevision((previous) => previous + 1)] as const;
}

const requestIndex = (_key: null, signal: AbortSignal) => loadIndex(signal);

function selectionFromUrl() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  return { comparison: params.get("comparison"), caseId: params.get("case") };
}

function hashFor(comparison: string, caseId?: string) {
  const params = new URLSearchParams({ comparison });
  if (caseId) params.set("case", caseId);
  return `#${params.toString()}`;
}

function useSelection() {
  const [selection, setSelection] = useState(selectionFromUrl);
  useEffect(() => {
    const onHashChange = () => setSelection(selectionFromUrl());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return selection;
}

function History({ index, active }: { index: Resource<ComparisonEntry[]>; active: string | undefined }) {
  return (
    <aside className="sidebar" aria-label="Evaluation console">
      <div className="brand">
        <span className="brand-mark"><Icon name="layers" /></span>
        <div><strong>LLM Regression Lab</strong><span>Evaluation console</span></div>
      </div>
      <nav className="run-history" aria-labelledby="history-heading">
        <div className="history-heading">
          <h2 id="history-heading">Run history</h2>
          {index.status === "ready" && <span>{index.data.length}</span>}
        </div>
        {index.status === "loading" && <p className="history-message">Loading comparisons…</p>}
        {index.status === "error" && <p className="history-message">Run history is unavailable.</p>}
        {index.status === "ready" && (index.data.length ? (
          <ul>
            {index.data.map((entry) => (
              <li key={entry.id}>
                <a className="run-link" aria-current={entry.id === active ? "page" : undefined} href={hashFor(entry.id)}>
                  <span className="run-top"><VerdictBadge verdict={entry.verdict} /><ModeBadge mode={entry.mode} /></span>
                  <strong>{entry.title}</strong>
                  <span className="run-bottom">
                    <span>{entry.case_count} {entry.case_count === 1 ? "case" : "cases"}</span>
                    <span title="Accuracy change">{entry.delta === null ? "Δ unavailable" : formatPoints(entry.delta)}</span>
                  </span>
                </a>
              </li>
            ))}
          </ul>
        ) : <p className="history-message">No comparisons yet.</p>)}
      </nav>
      <footer className="sidebar-footer">
        <Icon name="source" />
        <div><strong>Artifact source</strong><span>{sourceLabel}</span></div>
      </footer>
    </aside>
  );
}

function StatePanel({ title, description, action, loading = false }: { title: string; description: string; action?: ReactNode; loading?: boolean }) {
  return (
    <section className="state-panel panel" role={loading ? "status" : undefined}>
      <div className="state-icon">{loading ? <span className="spinner" /> : <Icon name="source" />}</div>
      <h1>{title}</h1>
      <p>{description}</p>
      {action}
    </section>
  );
}

function ComparisonView({ entry, requestedCase }: { entry: ComparisonEntry; requestedCase: string | null }) {
  const [resource, retry] = useResource(entry, loadComparison);
  if (resource.status === "loading") return <StatePanel title={entry.title} description="Loading comparison artifact…" loading />;
  if (resource.status === "error") {
    return <StatePanel title="Could not open comparison" description={resource.message} action={<button type="button" className="action-button" onClick={retry}>Retry comparison</button>} />;
  }
  return <LoadedComparison comparison={resource.data} requestedCase={requestedCase} />;
}

function LoadedComparison({ comparison, requestedCase }: { comparison: Comparison; requestedCase: string | null }) {
  const defaultCase = comparison.cases.find((item) => caseChange(item) === "Regressed")
    ?? comparison.cases.find((item) => item.candidate.some((trial) => trial.status !== "error" && !trial.passed))
    ?? comparison.cases.find((item) => caseChange(item) === "Incomplete")
    ?? comparison.cases[0];
  const requested = comparison.cases.find((item) => item.id === requestedCase);
  const selected = requested ?? defaultCase;
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const focusCase = useRef(Boolean(requested));

  useEffect(() => {
    if (focusCase.current && detailHeading.current) {
      detailHeading.current.focus();
      focusCase.current = false;
    }
  }, [requestedCase]);

  function openCase(event: MouseEvent<HTMLAnchorElement>) {
    if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.currentTarget.hash === window.location.hash) {
      event.preventDefault();
      detailHeading.current?.focus();
    } else {
      focusCase.current = true;
    }
  }

  const modeDescription = comparison.mode === "fixture"
    ? "Recorded fixture outputs. Token usage, timing, and cost are illustrative."
    : "Live-mode comparison. Delivery and usage are recorded when available.";

  return (
    <>
      <header className="comparison-header">
        <div className="comparison-top"><span><Icon name="layers" /> Comparison</span><ModeBadge mode={comparison.mode} /></div>
        <h1>{comparison.title}</h1>
        <div className="comparison-meta">
          <span className="run-id">{comparison.id}</span>
          <span>{comparison.summary.case_count} {comparison.summary.case_count === 1 ? "case" : "cases"}</span>
          <p>{modeDescription}</p>
        </div>
      </header>
      <Overview comparison={comparison} />
      <CaseTable cases={comparison.cases} selected={selected?.id} getLink={(caseId) => hashFor(comparison.id, caseId)} openCase={openCase} />
      {requestedCase && !requested && <p className="case-notice" role="status">Case <code>{requestedCase}</code> is unavailable in this comparison.{selected ? ` Opened ${selected.id}.` : ""}</p>}
      {selected && <CaseDetail key={selected.id} evaluationCase={selected} headingRef={detailHeading} link={hashFor(comparison.id, selected.id)} />}
    </>
  );
}

export function App() {
  const [index, retry] = useResource(null, requestIndex);
  const { comparison, caseId } = useSelection();
  const entries = index.status === "ready" ? index.data : [];
  const active = comparison ? entries.find((entry) => entry.id === comparison) : entries[0];

  let content: ReactNode;
  if (index.status === "loading") {
    content = <StatePanel title="Run comparisons" description="Loading run history…" loading />;
  } else if (index.status === "error") {
    const description = sourceLabel === "Local JSON artifacts"
      ? `${index.message} Generate the demo artifacts or verify the local data feed.`
      : index.message;
    content = <StatePanel title="Could not load run history" description={description} action={<button type="button" className="action-button" onClick={retry}>Retry history</button>} />;
  } else if (!entries.length) {
    content = <StatePanel title="No comparisons yet" description="The artifact source is reachable. A comparison will appear here after the evaluator records a run." action={<button type="button" className="action-button" onClick={retry}>Refresh history</button>} />;
  } else if (!active) {
    content = <StatePanel title="Comparison unavailable" description={`The requested comparison, "${comparison}", is missing from run history.`} action={<a className="action-button" href={hashFor(entries[0].id)}>Open first comparison</a>} />;
  } else {
    content = <ComparisonView key={active.id} entry={active} requestedCase={caseId} />;
  }

  return (
    <>
      <a href="#workspace" className="skip-link" onClick={(event) => {
        event.preventDefault();
        document.getElementById("workspace")?.focus();
      }}>Skip to comparison</a>
      <div className="app-shell">
        <History index={index} active={active?.id} />
        <main id="workspace" className="workspace" tabIndex={-1}>
          <div className="workspace-content">{content}</div>
        </main>
      </div>
    </>
  );
}
