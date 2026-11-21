import { useState } from "react";
import type { RefObject } from "react";
import type { EvaluationCase, Mode, Trial } from "./data";
import { Icon } from "./Icons";
import { caseChange, formatCost, formatCount, formatLatency, labelize, summarizeTrials } from "./presentation";

export function ModeBadge({ mode }: { mode: Mode }) {
  return <span className={`pill mode-${mode}`}>{mode === "fixture" ? "Fixture" : "Live"}</span>;
}

export function ChangeBadge({ evaluationCase }: { evaluationCase: EvaluationCase }) {
  const change = caseChange(evaluationCase);
  return <span className={`pill change-${change.toLowerCase()}`}>{change}</span>;
}

function passCount(trials: Trial[]) {
  if (!trials.length) return "No trials";
  const { passed, scored, errors } = summarizeTrials(trials);
  const score = scored ? `${passed} of ${scored} scored trials passed` : "No scored trials";
  return errors ? `${score} · ${errors} ${errors === 1 ? "error" : "errors"}` : score;
}

function TrialResult({ title, trial, trials }: { title: string; trial: Trial | undefined; trials: Trial[] }) {
  const scored = trial?.status !== "error";
  const assertions = scored ? trial?.assertions ?? [] : [];
  const failed = assertions.filter((assertion) => !assertion.passed);
  const passed = assertions.filter((assertion) => assertion.passed);
  const status = trial?.status === "error" ? "Trial error"
    : trial?.status === "invalid" ? "Invalid output" : trial?.passed ? "Passed" : "Failed";

  return (
    <section className="trial-panel">
      <header className="trial-header">
        <div>
          <h3>{title}</h3>
          <div className="trial-total">{passCount(trials)}</div>
        </div>
        {trial && <span className={`pill ${trial.passed && trial.status === "ok" ? "verdict-pass" : "verdict-fail"}`}>{status}</span>}
      </header>
      {trial ? (
        <>
          <div className="trial-content">
            <h4>Raw provider output</h4>
            {trial.error && <p className="provider-error"><Icon name="alert" /><span>{trial.error}</span></p>}
            {trial.text
              ? <pre className="raw-output" tabIndex={0} role="region" aria-label={`${title} raw provider output`}><code>{trial.text}</code></pre>
              : <p className="no-output">No output returned.</p>}
          </div>
          <div className="trial-content">
            <h4>Trial trace</h4>
            <dl className="trace">
              <div><dt>Input tokens</dt><dd>{formatCount(trial.input_tokens)}</dd></div>
              <div><dt>Output tokens</dt><dd>{formatCount(trial.output_tokens)}</dd></div>
              <div><dt>Latency</dt><dd>{formatLatency(trial.latency_ms)}</dd></div>
              <div><dt>Cost (USD)</dt><dd>{formatCost(trial.cost_usd)}</dd></div>
              {trial.guardrail && <div><dt>Runtime guardrail</dt><dd>{labelize(trial.guardrail)}</dd></div>}
              {trial.stop_reason && <div><dt>Stop reason</dt><dd>{trial.stop_reason}</dd></div>}
              {trial.model && <div><dt>Model</dt><dd>{trial.model}</dd></div>}
              {trial.request_id && <div><dt>Request ID</dt><dd>{trial.request_id}</dd></div>}
            </dl>
          </div>
          <div className="trial-content assertion-content">
            <h4>Assertions{assertions.length > 0 && <span className="assertion-count">{passed.length} / {assertions.length} passed</span>}</h4>
            {failed.length > 0 && (
              <ul className="assertions">
                {failed.map((assertion, index) => (
                  <li key={`${assertion.name}-${index}`}>
                    <Icon name="close" />
                    <div><strong>{labelize(assertion.name)}</strong><span>{assertion.detail}</span></div>
                  </li>
                ))}
              </ul>
            )}
            {passed.length > 0 && (
              <details className="passed-assertions">
                <summary>{passed.length} passing {passed.length === 1 ? "assertion" : "assertions"}</summary>
                <ul className="assertions">
                  {passed.map((assertion, index) => (
                    <li key={`${assertion.name}-${index}`}>
                      <Icon name="check" />
                      <div><strong>{labelize(assertion.name)}</strong><span>{assertion.detail}</span></div>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {assertions.length === 0 && <p className="muted">{scored
              ? "No assertion results recorded."
              : "Assertions were unavailable for this trial."}</p>}
          </div>
        </>
      ) : <p className="missing-trial muted">The selected trial is unavailable for {title.toLowerCase()}.</p>}
    </section>
  );
}

interface CaseDetailProps {
  evaluationCase: EvaluationCase;
  headingRef: RefObject<HTMLHeadingElement | null>;
  link: string;
}

export function CaseDetail({ evaluationCase, headingRef, link }: CaseDetailProps) {
  const { baseline, candidate, expected, passages } = evaluationCase;
  const indices = [...new Set([...baseline, ...candidate].map((trial) => trial.index))].sort((first, second) => first - second);
  const baselinePassed = new Set(baseline.filter((trial) => trial.passed).map((trial) => trial.index));
  const candidateFailures = candidate.filter((trial) => trial.status !== "error" && !trial.passed);
  const regression = candidateFailures.find((trial) => baselinePassed.has(trial.index));
  const trialError = candidate.find((trial) => trial.status === "error") ?? baseline.find((trial) => trial.status === "error");
  const [chosenIndex, setChosenIndex] = useState(regression?.index ?? candidateFailures[0]?.index ?? trialError?.index ?? indices[0]);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "unavailable">("idle");

  async function copyCaseLink() {
    try {
      await navigator.clipboard.writeText(new URL(link, window.location.href).href);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("unavailable");
    }
  }

  return (
    <section aria-labelledby="case-detail-title" className="panel inspection">
      <header className="section-header">
        <div className="section-titles">
          <span className="case-id">{evaluationCase.id}</span>
          <h2 id="case-detail-title" ref={headingRef} tabIndex={-1}>Case investigation</h2>
        </div>
        <div className="section-actions">
          <ChangeBadge evaluationCase={evaluationCase} />
          {navigator.clipboard
            ? <button type="button" className="quiet-button" onClick={copyCaseLink}><Icon name="link" /> Copy link</button>
            : <a className="quiet-button" href={link}><Icon name="link" /> Case link</a>}
        </div>
      </header>
      {copyStatus !== "idle" && (
        <p className="copy-message" role="status">{copyStatus === "unavailable"
          ? <>Clipboard unavailable. <a href={link}>Open the case link</a> to copy its address.</>
          : "Case link copied."}</p>
      )}
      <div className="case-context">
        <div className="case-question">
          <div className="case-meta"><span>{labelize(evaluationCase.category)}</span><span>{labelize(evaluationCase.cluster)}</span></div>
          <h3>Question</h3>
          <p>{evaluationCase.question}</p>
        </div>
        <div className="expected">
          <h3>Expected</h3>
          <dl className="expected-fields">
            <div><dt>Decision</dt><dd>{expected.decision}</dd></div>
            <div><dt>Value</dt><dd>{expected.value ?? "—"}</dd></div>
            {expected.evidence_ids && <div><dt>Evidence IDs</dt><dd>{expected.evidence_ids.join(", ") || "—"}</dd></div>}
            {expected.customer_response !== undefined && <div><dt>Customer response</dt><dd>{expected.customer_response ?? "—"}</dd></div>}
          </dl>
        </div>
      </div>
      <section className="passages" aria-labelledby="passage-heading">
        <h3 id="passage-heading"><Icon name="source" /> Source passages <span>{passages.length}</span></h3>
        {passages.length ? <div className="passage-list">
          {passages.map((passage) => (
            <details key={passage.id} className="passage">
              <summary><code>{passage.id}</code>{expected.evidence_ids?.includes(passage.id) && <span className="pill evidence-badge">Expected evidence</span>}</summary>
              <p>{passage.text}</p>
            </details>
          ))}
        </div> : <p className="muted">No passages recorded.</p>}
      </section>
      <div className="trial-comparison">
        <div className="trial-controls">
          <h3>Trial comparison</h3>
          {indices.length > 1 && (
            <label className="compact-select">Paired trial
              <span className="select-field">
                <select value={chosenIndex} onChange={(event) => setChosenIndex(Number(event.target.value))}>
                  {indices.map((index) => <option key={index} value={index}>Trial {index}</option>)}
                </select>
                <Icon name="chevron" />
              </span>
            </label>
          )}
        </div>
        {indices.length ? <div className="trial-grid">
          <TrialResult title="Baseline" trial={baseline.find((trial) => trial.index === chosenIndex)} trials={baseline} />
          <TrialResult title="Candidate" trial={candidate.find((trial) => trial.index === chosenIndex)} trials={candidate} />
        </div> : <p className="muted">No trials recorded for this case.</p>}
      </div>
    </section>
  );
}
