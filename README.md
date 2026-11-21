# LLM Regression & Guardrail Testing Suite

A support assistant can give the right product code, cite the wrong manual, and promise a customer more than the policy allows. This Python evaluation harness tests those failures separately and gives GitHub Actions a release decision with the evidence behind it.

[Open the investigation console](https://alekwayne.dev/llm-regression-suite/) · [Inspect the CI runs](https://github.com/alek-wayne/llm-regression-suite/actions/workflows/regression.yml)

## A regression you can reproduce

The console opens on the repaired run. Both examples use the same 48 labeled questions across 12 fictional store-policy families and exercise the same graders, runtime guard, measurements, and gate as the Anthropic adapter. The fixture responses, timing, and token counts are synthetic. The suite computes the grades.

| Fixture comparison | Baseline | Candidate | Accuracy change | Gate |
| --- | ---: | ---: | ---: | --- |
| [Repaired candidate](https://alekwayne.dev/llm-regression-suite/#comparison=repaired) | 48/48 | 48/48 | 0.00 percentage points | PASS |
| [Seeded regression](https://alekwayne.dev/llm-regression-suite/#comparison=regressed) | 48/48 | 40/48 | −16.67 percentage points | FAIL |

In the seeded run, **CMP-02** asks whether a Desk Display accepts a Plate C mount. The candidate returns the correct code but cites the Cart Display policy and promises compatibility with every other accessory. JSON structure, decision, and value all pass. Attribution and approved wording fail. The runtime guard withholds the reply. In the repaired run, the candidate cites the Desk Display policy and passes all checks. **RET-04** illustrates a different failure. Its quote is genuine, but the rule applies to a different purchaser. The offline grader catches the scope error. [Inspect the seeded CMP-02](https://alekwayne.dev/llm-regression-suite/#comparison=regressed&case=CMP-02) · [Inspect the repaired CMP-02](https://alekwayne.dev/llm-regression-suite/#comparison=repaired&case=CMP-02)

[![Regression console showing the repaired candidate and its passing release gate](docs/console.png)](https://alekwayne.dev/llm-regression-suite/)

## Run it locally

The evaluation requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.13, which uv can install. The console also uses Node.js 24 and npm. Run the commands from the repository root.

```sh
uv sync --locked
uv run --locked python -m evalsuite demo
uv run --locked python -m evalsuite replay artifacts/demo/repaired/comparison.json
```

Each demo writes a self-contained JSON comparison and a Markdown report under `artifacts/demo/`. Replay reads that JSON, verifies its content identity, regrades every trial, reprices standard token usage, and recomputes the verdict. It works offline once dependencies are installed.

```sh
# Start the local console at http://127.0.0.1:8765
make preview

# Exercise the failing gate directly. This command intentionally exits with status 1.
uv run --locked python -m evalsuite compare --scenario regressed --out artifacts/failure

# Inspect one case or replay a downloaded CI artifact.
uv run --locked python -m evalsuite compare --case CMP-02 --scenario regressed
uv run --locked python -m evalsuite replay artifacts/failure/comparison.json
```

The public demonstration pull request changes the candidate recordings, then restores them. Its first revision fails the accuracy gate. The repair also adds a grader test for a correct answer citing the wrong retrieved document. [Failed GitHub Actions run](https://github.com/alek-wayne/llm-regression-suite/actions/runs/44848877522) · [Repair checks](https://github.com/alek-wayne/llm-regression-suite/pull/1/checks) · [Download the replayable reports](https://github.com/alek-wayne/llm-regression-suite/releases/tag/v1.0.0)

## What the suite measures

| Signal | Definition |
| --- | --- |
| JSON contract | One complete object, exact field types and enum values, no duplicate keys, and valid combinations of decision, value, and evidence. |
| Decision and value | Exact disposition and policy code. Missing customer information calls for clarification. Missing or unresolved conflicting policy calls for abstention. |
| Attribution | The full set of supporting passage IDs. Extra, missing, duplicate, and wrong-scope sources fail independently of the answer code. |
| Customer wording | Exact approved text for the labeled outcome. This closed vocabulary makes invented promises mechanically detectable. A harmless paraphrase also fails this contract. |
| Runtime guard | Validates response shape and checks cited IDs and approved wording against the retrieved passages. It has no access to the answer key. |
| Latency | Per-arm p50 and p95 over delivered responses, using linear interpolation. Live wall time includes free token preflight and the Messages call. |
| Cost and availability | Provider token counts and Decimal list-price accounting. Reports distinguish known spend from trials with unknown charges. Refusal or truncation fails quality. Transport failures make a comparison inconclusive. |

A trial passes when the reply clears the runtime guard and every assertion. Strict accuracy averages trial success within each case, then averages cases equally. The console also shows the underlying repetitions. Baseline and candidate run in alternating order. The reported 95% interval uses 2,000 seeded paired bootstrap resamples of whole policy families. With 12 curated families and one synthetic trial per arm in the demo, that interval describes sensitivity to this corpus composition. Live reliability and production latency require separate measurements.

### Gate policy

| Condition | Verdict and process exit |
| --- | --- |
| Missing or stale evidence, unavailable telemetry, provider failure, or unmatched repetitions | INCONCLUSIVE · 2 |
| Candidate accuracy below 90%, p95 above 5,000 ms, or any candidate trial above $0.01 | FAIL · 1 |
| Any fixture accuracy drop, or a live drop greater than 5 percentage points | FAIL · 1 |
| Complete comparison within the limits | PASS · 0 |

The live cutoffs are initial engineering thresholds. Repeat baseline-against-baseline runs before using them for a production release decision. The public demos and CI evidence use keyless fixtures.

## How CI works

Pull requests, pushes to main, and merge-queue runs exercise the keyless gate. On a pull request the approved baseline comes from its target revision. Frozen observations are bound to hashes of the exact model-visible request, including the system prompt, ordered passages, JSON schema, and model. A prompt edit invalidates its old evidence. A fresh fixture must be authored and reviewed for that request, or a separately authorized live run can measure it.

GitHub Actions writes the verdict to its job summary and uploads `comparison.json` and `report.md`, including on a failed gate. Anyone with the downloaded artifact can replay it. Pull requests receive read-only repository permissions. A separately dispatched main-branch workflow accepts an Anthropic key from the `live-evaluation` environment. Hosting the console uses its own Pages workflow.

## PostgreSQL and React

```sh
# Start PostgreSQL 18, import both examples, build the React client, and serve it on port 8765.
make db-demo
```

Docker Compose binds the demo database to loopback on port 5488. Override ports with `make db-demo DB_PORT=5489 PORT=8766`. A transactional importer stores the verified artifact, its trials, and individual assertions. Identical concurrent imports produce one copy. The `arm_usage` SQL view independently calculates p95 and known cost. The read-only FastAPI service exposes comparison history, complete comparisons, and individual cases. The public console reads the same artifact format as static JSON.

```sh
# Run the evaluator, PostgreSQL, and console build checks.
docker compose up -d --wait db
TEST_DATABASE_URL=postgresql://eval:eval@127.0.0.1:5488/evalsuite make check

# Test the built console in desktop and mobile Chromium.
npm exec --prefix web -- playwright install --only-shell chromium
npm run test:navigation --prefix web
```

## Anthropic integration

The optional live adapter uses the pinned `claude-haiku-5-5` Messages API, native structured output, a token preflight, and an explicit per-run budget. It sends only the customer question and retrieved policy passages alongside the system instructions. The grader owns labels and expected evidence. SDK transport tests cover the serialized requests, multi-block responses, failed delivery, timeouts, and uncertain billing. A paid run requires an operator-provided `ANTHROPIC_API_KEY`.

```sh
# Optional paid example after configuring an API key in the environment.
uv run --locked python -m evalsuite compare --mode live --case CMP-02 --trials 1 --budget-usd 0.01
```

The rate card travels with each comparison. Anthropic documents the model ID, structured-output contract, and current prices. [Model overview](https://platform.claude.com/docs/en/models/haiku-5-5/overview) · [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) · [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)

## Source map

| Area | Where to start |
| --- | --- |
| Cases, prompts, and frozen observations | [Policy corpus](corpus/cases.json), [candidate prompt](prompts/candidate.md), [fixtures](fixtures/) |
| Request, guard, and scoring | [Provider](src/evalsuite/provider.py), [runtime guard](src/evalsuite/guardrails.py), [grader](src/evalsuite/grading.py) |
| Comparison, evidence, and release decision | [Runner](src/evalsuite/runner.py), [evidence schema](src/evalsuite/evidence.py), [metrics](src/evalsuite/metrics.py) |
| Storage and investigation | [SQL schema](src/evalsuite/sql/001_initial.sql), [read API](src/evalsuite/api.py), [React console](web/src/) |
| Verification | [Tests](tests/), [GitHub Actions workflows](.github/workflows/) |
