import type { EvaluationCase, Trial } from "./data";

const numberFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const pointFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const costFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 12,
});

export function formatAccuracy(fraction: number | null) {
  return fraction === null ? "—" : `${numberFormatter.format(fraction * 100)}%`;
}

export function formatLatency(ms: number | null) {
  return ms === null ? "—" : `${numberFormatter.format(ms)} ms`;
}

export function formatCount(value: number | null) {
  return value === null ? "—" : numberFormatter.format(value);
}

function costAmount(raw: string | null) {
  if (raw === null || !raw.trim()) return null;
  const amount = Number(raw);
  return Number.isFinite(amount) ? amount : null;
}

export function formatCost(raw: string | null) {
  const amount = costAmount(raw);
  return amount === null ? "—" : `$${costFormatter.format(amount)}`;
}

function sign(value: number, rendered: string, isZero: boolean) {
  if (isZero) return rendered;
  return `${value > 0 ? "+" : "−"}${rendered}`;
}

export function formatPoints(delta: number) {
  const points = delta * 100;
  const magnitude = pointFormatter.format(Math.abs(points));
  return `${sign(points, magnitude, magnitude === "0")} pp`;
}

export function formatLatencyChange(baseline: number | null, candidate: number | null) {
  if (baseline === null || candidate === null) return null;
  const change = candidate - baseline;
  const magnitude = numberFormatter.format(Math.abs(change));
  return `${sign(change, magnitude, magnitude === "0")} ms`;
}

export function formatCostChange(baseline: string | null, candidate: string | null) {
  const baselineAmount = costAmount(baseline);
  const candidateAmount = costAmount(candidate);
  if (baselineAmount === null || candidateAmount === null) return null;
  const change = candidateAmount - baselineAmount;
  const magnitude = costFormatter.format(Math.abs(change));
  return sign(change, `$${magnitude}`, magnitude === "0.00");
}

export function summarizeTrials(trials: Trial[]) {
  const scored = trials.filter((trial) => trial.status !== "error");
  return { passed: scored.filter((trial) => trial.passed).length, scored: scored.length, errors: trials.length - scored.length };
}

export function caseChange(evaluationCase: EvaluationCase) {
  const { baseline, candidate } = evaluationCase;
  const baselineScore = summarizeTrials(baseline);
  const candidateScore = summarizeTrials(candidate);
  if (!baselineScore.scored || !candidateScore.scored || baselineScore.errors || candidateScore.errors) return "Incomplete";
  const baselinePass = baselineScore.passed * candidateScore.scored;
  const candidatePass = candidateScore.passed * baselineScore.scored;
  if (candidatePass > baselinePass) return "Improved";
  if (candidatePass < baselinePass) return "Regressed";
  return "Unchanged";
}

export function labelize(label: string) {
  const words = label.replaceAll(/[_-]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
