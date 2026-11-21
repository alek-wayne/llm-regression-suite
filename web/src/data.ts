export type Verdict = "PASS" | "FAIL" | "INCONCLUSIVE";
export type Mode = "fixture" | "live";

export interface ComparisonEntry {
  id: string;
  title: string;
  mode: Mode;
  verdict: Verdict;
  delta: number | null;
  case_count: number;
  artifact: string;
}

export interface Assertion {
  name: string;
  passed: boolean;
  detail: string;
}

export interface Trial {
  index: number;
  text: string;
  status: "ok" | "invalid" | "error";
  guardrail?: string | null;
  model?: string;
  request_id?: string | null;
  stop_reason?: string | null;
  error: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  cost_usd: string | null;
  passed: boolean;
  assertions: Assertion[];
}

export interface Expected {
  decision: string;
  value: string | null;
  evidence_ids?: string[] | null;
  customer_response?: string | null;
}

export interface Passage {
  id: string;
  text: string;
}

export interface EvaluationCase {
  id: string;
  category: string;
  cluster: string;
  question: string;
  passages: Passage[];
  expected: Expected;
  baseline: Trial[];
  candidate: Trial[];
}

interface ArmSummary {
  unknown_cost_trials?: number;
}

export interface Summary {
  verdict: Verdict;
  delta: number | null;
  reasons: string[];
  case_count: number;
  baseline_accuracy: number | null;
  candidate_accuracy: number | null;
  baseline_p95_ms: number | null;
  candidate_p95_ms: number | null;
  baseline_cost_usd: string | null;
  candidate_cost_usd: string | null;
  ci95: [number, number] | null;
  arms?: { baseline: ArmSummary; candidate: ArmSummary };
}

export interface Comparison {
  version: 1;
  id: string;
  title: string;
  mode: Mode;
  summary: Summary;
  cases: EvaluationCase[];
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isText = (value: unknown): value is string => typeof value === "string";
const isNullableText = (value: unknown): value is string | null => value === null || isText(value);
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isNullableNumber = (value: unknown): value is number | null => value === null || isNumber(value);
const isVerdict = (value: unknown): value is Verdict => value === "PASS" || value === "FAIL" || value === "INCONCLUSIVE";
const isMode = (value: unknown): value is Mode => value === "fixture" || value === "live";

function isList<T>(value: unknown, validate: (item: unknown) => item is T): value is T[] {
  return Array.isArray(value) && value.every(validate);
}

function isEntry(value: unknown): value is ComparisonEntry {
  return isRecord(value) && isText(value.id) && isText(value.title)
    && isMode(value.mode) && isVerdict(value.verdict) && isNullableNumber(value.delta)
    && isNumber(value.case_count) && isText(value.artifact);
}

function isAssertion(value: unknown): value is Assertion {
  return isRecord(value) && isText(value.name) && typeof value.passed === "boolean" && isText(value.detail);
}

function isTrial(value: unknown): value is Trial {
  return isRecord(value) && isNumber(value.index) && isText(value.text)
    && (value.status === "ok" || value.status === "invalid" || value.status === "error")
    && (value.guardrail === undefined || isNullableText(value.guardrail)) && isNullableText(value.error)
    && (value.model === undefined || isText(value.model))
    && (value.request_id === undefined || isNullableText(value.request_id))
    && (value.stop_reason === undefined || isNullableText(value.stop_reason))
    && isNullableNumber(value.input_tokens) && isNullableNumber(value.output_tokens)
    && isNullableNumber(value.latency_ms) && isNullableText(value.cost_usd)
    && typeof value.passed === "boolean" && isList(value.assertions, isAssertion);
}

function isExpected(value: unknown): value is Expected {
  return isRecord(value) && isText(value.decision) && isNullableText(value.value)
    && (value.evidence_ids === undefined || value.evidence_ids === null || isList(value.evidence_ids, isText))
    && (value.customer_response === undefined || isNullableText(value.customer_response));
}

function isPassage(value: unknown): value is Passage {
  return isRecord(value) && isText(value.id) && isText(value.text);
}

function isCase(value: unknown): value is EvaluationCase {
  return isRecord(value) && isText(value.id) && isText(value.category) && isText(value.cluster)
    && isText(value.question) && isList(value.passages, isPassage) && isExpected(value.expected)
    && isList(value.baseline, isTrial) && isList(value.candidate, isTrial);
}

function isArmSummary(value: unknown): value is ArmSummary {
  return isRecord(value) && (value.unknown_cost_trials === undefined || isNumber(value.unknown_cost_trials));
}

function isSummary(value: unknown): value is Summary {
  return isRecord(value) && isVerdict(value.verdict) && isNullableNumber(value.delta) && isNumber(value.case_count)
    && isList(value.reasons, isText) && isNullableNumber(value.baseline_accuracy)
    && isNullableNumber(value.candidate_accuracy) && isNullableNumber(value.baseline_p95_ms)
    && isNullableNumber(value.candidate_p95_ms) && isNullableText(value.baseline_cost_usd)
    && isNullableText(value.candidate_cost_usd)
    && (value.ci95 === null || (isList(value.ci95, isNumber) && value.ci95.length === 2))
    && (value.arms === undefined || (isRecord(value.arms) && isArmSummary(value.arms.baseline) && isArmSummary(value.arms.candidate)));
}

function isComparison(value: unknown): value is Comparison {
  return isRecord(value) && value.version === 1 && isText(value.id) && isText(value.title)
    && isMode(value.mode) && isSummary(value.summary) && isList(value.cases, isCase);
}

const configuredApi = import.meta.env.VITE_API_BASE?.trim().replace(/\/+$/, "");
const apiRoot = configuredApi && (configuredApi.endsWith("/api") ? configuredApi : `${configuredApi}/api`);
const indexUrl = new URL("data/index.json", new URL(import.meta.env.BASE_URL, window.location.href));

export const sourceLabel = apiRoot ? "Read-only API" : "Local JSON artifacts";

async function readJson(url: string | URL, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(url, {
    signal,
    credentials: "omit",
    cache: "no-cache",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error("The requested artifact could not be found (HTTP 404).");
    }
    throw new Error(`The artifact source returned HTTP ${response.status}.`);
  }
  try {
    return await response.json() as unknown;
  } catch {
    throw new Error("The artifact source did not return valid JSON.");
  }
}

export async function loadIndex(signal: AbortSignal): Promise<ComparisonEntry[]> {
  const data = await readJson(apiRoot ? `${apiRoot}/comparisons` : indexUrl, signal);
  if (!isRecord(data) || !isList(data.comparisons, isEntry)) {
    throw new Error("The comparison index has an unsupported format.");
  }
  return data.comparisons;
}

export async function loadComparison(entry: ComparisonEntry, signal: AbortSignal): Promise<Comparison> {
  let url: string | URL;
  if (apiRoot) {
    url = `${apiRoot}/comparisons/${encodeURIComponent(entry.id)}`;
  } else {
    const artifactUrl = new URL(entry.artifact, indexUrl);
    const expectedUrl = new URL(`${encodeURIComponent(entry.id)}.json`, indexUrl);
    if (artifactUrl.href !== expectedUrl.href) {
      throw new Error("The index entry points outside its expected comparison artifact.");
    }
    url = artifactUrl;
  }
  const data = await readJson(url, signal);
  if (!isComparison(data) || data.id !== entry.id || data.mode !== entry.mode) {
    throw new Error("The selected comparison has an unsupported format or does not match its index entry.");
  }
  return data;
}
