import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { CaseDetail, ModeBadge } from "./CaseDetail";
import { CaseTable } from "./CaseTable";
import { loadComparison, loadIndex, sourceLabel, usesStaticArtifacts } from "./data";
import type { Comparison, ComparisonEntry } from "./data";
import { Icon } from "./Icons";
import { Overview, VerdictBadge } from "./Overview";
import { formatPoints } from "./presentation";

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

// These artifact IDs were published before scenario links were introduced.
const earlierDemoScenarios = new Map<string, ComparisonEntry["scenario"]>([
  ["fixture-5a6d675f9c59e8a9f5dbacc8", "repaired"],
  ["fixture-bca00797df094798b99c2fa7", "regressed"],
]);

type Selection = { comparison: string | null; caseId: string | null; inspect: boolean };
type Navigate = (event: MouseEvent<HTMLAnchorElement>, inspect?: boolean) => boolean;

function selectionFromUrl(inspect = false): Selection {
  const params = new URLSearchParams(window.location.hash.slice(1));
  return { comparison: params.get("comparison"), caseId: params.get("case"), inspect };
}

function hashFor(entry: ComparisonEntry, caseId?: string) {
  const params = new URLSearchParams({ comparison: entry.scenario ?? entry.id });
  if (caseId) params.set("case", caseId);
  return `#${params.toString()}`;
}

function useSelection() {
  const [selection, setSelection] = useState(() => selectionFromUrl());
  useEffect(() => {
    const syncSelection = () => setSelection(selectionFromUrl());
    window.addEventListener("popstate", syncSelection);
    window.addEventListener("hashchange", syncSelection);
    return () => {
      window.removeEventListener("popstate", syncSelection);
      window.removeEventListener("hashchange", syncSelection);
    };
  }, []);

  const navigate: Navigate = (event, inspect) => {
    if (event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
    event.preventDefault();
    if (event.currentTarget.hash !== window.location.hash) window.history.pushState(null, "", event.currentTarget.href);
    setSelection(selectionFromUrl(inspect));
    return true;
  };
  return [selection, navigate] as const;
}

interface HistoryProps {
  index: Resource<ComparisonEntry[]>;
  active: string | undefined;
  openComparison: (event: MouseEvent<HTMLAnchorElement>) => void;
}

function History({ index, active, openComparison }: HistoryProps) {
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
                <a className="run-link" aria-current={entry.id === active ? "page" : undefined} href={hashFor(entry)} onClick={openComparison}>
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

interface ComparisonViewProps {
  entry: ComparisonEntry;
  selection: Selection;
  navigate: Navigate;
}

function ComparisonView(props: ComparisonViewProps) {
  const { entry } = props;
  const [resource, retry] = useResource(entry, loadComparison);
  if (resource.status === "loading") return <StatePanel title={entry.title} description="Loading comparison artifact…" loading />;
  if (resource.status === "error") {
    return <StatePanel title="Could not open comparison" description={resource.message} action={<button type="button" className="action-button" onClick={retry}>Retry comparison</button>} />;
  }
  return <LoadedComparison {...props} comparison={resource.data} />;
}

function LoadedComparison({ comparison, entry, selection, navigate }: ComparisonViewProps & { comparison: Comparison }) {
  const requested = comparison.cases.find((item) => item.id === selection.caseId);
  const selected = selection.inspect ? requested : undefined;
  const detailHeading = useRef<HTMLHeadingElement>(null);

  useLayoutEffect(() => {
    if (selection.inspect) detailHeading.current?.focus();
  }, [selection]);

  const getLink = (caseId: string) => hashFor(entry, caseId);
  function openCase(event: MouseEvent<HTMLAnchorElement>) {
    navigate(event, true);
  }

  const modeDescription = comparison.mode === "fixture"
    ? "Synthetic fixture responses. Token usage, timing, and cost are illustrative."
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
          {entry.scenario === "repaired" && <p>The candidate corrects the responses from the seeded regression and clears the release gate.</p>}
          {entry.scenario === "regressed" && <p>Deliberately flawed candidate responses test the release gate. Open a regressed case to see which checks failed.</p>}
        </div>
        {requested && <a className="action-button case-inspect-action" href={getLink(requested.id)} onClick={openCase}>Inspect {requested.id}</a>}
        {selection.caseId && !requested && <p className="case-notice" role="status">Case <code>{selection.caseId}</code> is unavailable in this comparison.</p>}
      </header>
      <Overview comparison={comparison} />
      <CaseTable cases={comparison.cases} selected={selected?.id} getLink={getLink} openCase={openCase} />
      {selected && <CaseDetail key={selected.id} evaluationCase={selected} headingRef={detailHeading} link={getLink(selected.id)} />}
    </>
  );
}

export function App() {
  const [index, retry] = useResource(null, requestIndex);
  const [selection, navigate] = useSelection();
  const { comparison } = selection;
  const entries = index.status === "ready" ? index.data : [];
  const active = comparison
    ? entries.find((entry) => entry.id === comparison) ?? entries.find((entry) => entry.scenario === comparison)
    : entries[0];

  function openComparison(event: MouseEvent<HTMLAnchorElement>) {
    if (!navigate(event)) return;
    const heading = document.querySelector("#workspace h1");
    if (heading && heading.getBoundingClientRect().top < 0) window.scrollTo(0, 0);
  }

  let content: ReactNode;
  if (index.status === "loading") {
    content = <StatePanel title="Run comparisons" description="Loading run history…" loading />;
  } else if (index.status === "error") {
    const description = usesStaticArtifacts
      ? `${index.message} Generate the demo artifacts or verify the static data feed.`
      : index.message;
    content = <StatePanel title="Could not load run history" description={description} action={<button type="button" className="action-button" onClick={retry}>Retry history</button>} />;
  } else if (!entries.length) {
    content = <StatePanel title="No comparisons yet" description="The artifact source is reachable. A comparison will appear here after the evaluator records a run." action={<button type="button" className="action-button" onClick={retry}>Refresh history</button>} />;
  } else if (!active) {
    const earlierScenario = usesStaticArtifacts && comparison ? earlierDemoScenarios.get(comparison) : undefined;
    const replacement = earlierScenario ? entries.find((entry) => entry.scenario === earlierScenario) : undefined;
    content = replacement ? (
      <StatePanel title="Earlier demo comparison" description="This link points to an earlier demo artifact. The current example has a different artifact ID."
        action={<a className="action-button" href={hashFor(replacement, selection.caseId ?? undefined)} onClick={openComparison}>
          {earlierScenario === "repaired" ? "Open current repaired example" : "Open current seeded example"}
        </a>} />
    ) : (
      <StatePanel title="Comparison unavailable" description={`The requested comparison, "${comparison}", is missing from run history.`}
        action={<a className="action-button" href={hashFor(entries[0])} onClick={openComparison}>Open first comparison</a>} />
    );
  } else {
    content = <ComparisonView key={active.id} entry={active} selection={selection} navigate={navigate} />;
  }

  return (
    <>
      <a href="#workspace" className="skip-link" onClick={(event) => {
        event.preventDefault();
        document.getElementById("workspace")?.focus();
      }}>Skip to comparison</a>
      <div className="app-shell">
        <History index={index} active={active?.id} openComparison={openComparison} />
        <main id="workspace" className="workspace" tabIndex={-1}>
          <div className="workspace-content">{content}</div>
        </main>
      </div>
    </>
  );
}
