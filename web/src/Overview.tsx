import type { Comparison, Verdict } from "./data";
import { Icon } from "./Icons";
import { formatAccuracy, formatCost, formatCostChange, formatLatency, formatLatencyChange, formatPoints } from "./presentation";

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return <span className={`pill verdict-${verdict.toLowerCase()}`}>{verdict}</span>;
}

interface MetricProps {
  title: string;
  baseline: string;
  candidate: string;
  delta: string | null;
  tone?: "good" | "bad" | "quiet";
  accuracy?: [number | null, number | null];
}

function Metric({ title, baseline, candidate, delta, tone = "quiet", accuracy }: MetricProps) {
  return (
    <section className="metric panel">
      <header className="metric-header">
        <h2>{title}</h2>
        {delta && <span className={`metric-delta metric-delta-${tone}`} aria-label={`Change ${delta}`}>{delta}</span>}
      </header>
      <dl className="metric-pair">
        <div><dt>Baseline</dt><dd>{baseline}</dd></div>
        <div><dt>Candidate</dt><dd>{candidate}</dd></div>
      </dl>
      {accuracy && (
        <div className="accuracy-bars" aria-hidden="true">
          {accuracy.map((fraction, index) => (
            <span key={index}><span style={{ inlineSize: `${Math.max(0, Math.min((fraction ?? 0) * 100, 100))}%` }} /></span>
          ))}
        </div>
      )}
    </section>
  );
}

export function Overview({ comparison }: { comparison: Comparison }) {
  const { summary } = comparison;
  const accuracyTone = (summary.delta ?? 0) < 0 ? "bad" : (summary.delta ?? 0) > 0 ? "good" : "quiet";
  const accuracyDelta = summary.delta === null ? null : formatPoints(summary.delta);
  const baselineCost = summary.arms?.baseline.unknown_cost_trials ? null : summary.baseline_cost_usd;
  const candidateCost = summary.arms?.candidate.unknown_cost_trials ? null : summary.candidate_cost_usd;
  const hasMissingMetrics = [summary.baseline_accuracy, summary.candidate_accuracy, summary.baseline_p95_ms,
    summary.candidate_p95_ms, baselineCost, candidateCost].includes(null);

  return (
    <div className="overview">
      <section className={`panel gate gate-${summary.verdict.toLowerCase()}`} aria-labelledby="gate-heading">
        <div className="gate-main">
          <span className="gate-icon"><Icon name={summary.verdict === "PASS" ? "check" : "alert"} /></span>
          <div>
            <h2 id="gate-heading">Release gate</h2>
            <VerdictBadge verdict={summary.verdict} />
          </div>
        </div>
        <div className="gate-reasons">
          {summary.reasons.length ? <ul>{summary.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p>No gate reasons recorded.</p>}
        </div>
        <div className="gate-ci">
          <span>95% CI for accuracy change</span>
          <strong>{summary.ci95 ? `${formatPoints(summary.ci95[0])} to ${formatPoints(summary.ci95[1])}` : "Unavailable"}</strong>
        </div>
      </section>
      <div className="metric-grid">
        <Metric title="Accuracy"
          baseline={formatAccuracy(summary.baseline_accuracy)}
          candidate={formatAccuracy(summary.candidate_accuracy)}
          delta={accuracyDelta} tone={accuracyTone}
          accuracy={[summary.baseline_accuracy, summary.candidate_accuracy]} />
        <Metric title="P95 latency"
          baseline={formatLatency(summary.baseline_p95_ms)}
          candidate={formatLatency(summary.candidate_p95_ms)}
          delta={formatLatencyChange(summary.baseline_p95_ms, summary.candidate_p95_ms)} />
        <Metric title="Total cost (USD)"
          baseline={formatCost(baselineCost)}
          candidate={formatCost(candidateCost)}
          delta={formatCostChange(baselineCost, candidateCost)} />
      </div>
      {hasMissingMetrics && <p className="metric-note muted">An em dash indicates unavailable measurements.</p>}
    </div>
  );
}
