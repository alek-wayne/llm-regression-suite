"""Collect paired evidence once and independently regrade the saved observations."""

import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field

from .domain import Case, load_cases
from .evidence import (
    ARMS,
    Arm,
    CaseResult,
    Comparison,
    Observation,
    Policy,
    Record,
    Trial,
    artifact_id,
    digest,
    money,
)
from .grading import ASSERTION_NAMES, Assertion, grade
from .guardrails import runtime_guard
from .metrics import summarize
from .provider import MODEL, RATE_INPUT, RATE_OUTPUT, LiveProvider, cost_usd, request_body

Mode = Literal["fixture", "live"]
Scenario = Literal["repaired", "regressed"]


class Fixture(Record):
    text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_revision(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def has_input_changes(root: Path) -> bool:
    status = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--",
            "corpus",
            "prompts",
            "fixtures",
            "src",
            "uv.lock",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(status.stdout.strip()) or status.returncode != 0


def failed_observation(reason: str, *, known_cost: str | None = None) -> Observation:
    return Observation(
        text="",
        status="error",
        error=reason,
        input_tokens=None,
        output_tokens=None,
        latency_ms=None,
        cost_usd=known_cost,
        model=MODEL,
        request_id=None,
        stop_reason=None,
    )


def evaluate(case: Case, record: Observation, index: int) -> Trial:
    if record.status == "ok":
        assertions = grade(case, record.text)
        guard = runtime_guard(case.passages, record.text)
    else:
        guard = "withheld_delivery"
        assertions = [
            Assertion(name=name, passed=False, detail=record.error or "No complete response")
            for name in ASSERTION_NAMES
        ]
    return Trial(
        **record.model_dump(),
        index=index,
        guardrail=guard,
        assertions=assertions,
        passed=guard == "served" and all(item.passed for item in assertions),
    )


def request_manifest(cases: list[Case], prompts: dict[Arm, str]) -> dict:
    return {
        arm: {case.id: digest(request_body(case, prompts[arm])) for case in cases} for arm in ARMS
    }


def read_prompts(root: Path, reference: Path) -> dict[Arm, str]:
    return {
        "baseline": (reference / "prompts/baseline.md").read_text(encoding="utf-8"),
        "candidate": (root / "prompts/candidate.md").read_text(encoding="utf-8"),
    }


class Fixtures:
    def __init__(self, root: Path, reference: Path, scenario: Scenario, case_ids: set[str]):
        self.recordings = {}
        self.hashes = {}
        self.errors = {}
        for arm, directory in (("baseline", reference), ("candidate", root)):
            try:
                records = load_json(directory / f"fixtures/{arm}.json")
                hashes = load_json(directory / "fixtures/requests.lock.json")[arm]
                if not isinstance(records, dict) or not isinstance(hashes, dict):
                    raise TypeError(
                        "Expected case IDs mapped to frozen observations and request hashes"
                    )
                if (set(records) | set(hashes)) - case_ids:
                    raise ValueError(
                        "The corpus dropped a case still present in the frozen evidence"
                    )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.errors[arm] = f"Invalid {arm} fixture or request lock: {exc}"
                records, hashes = {}, {}
            self.recordings[arm], self.hashes[arm] = records, hashes
        if scenario == "regressed":
            try:
                overrides = load_json(root / "fixtures/regressed.json")
                if not isinstance(overrides, dict) or not set(overrides) <= case_ids:
                    raise ValueError("Regression overrides must map known case IDs to recordings")
                self.recordings["candidate"].update(overrides)
            except (ValueError, OSError) as exc:
                self.errors["candidate"] = f"Invalid regression override: {exc}"

    def observe(
        self, case: Case, arm: Arm, index: int, planned: int, request_hash: str
    ) -> Observation:
        if arm in self.errors:
            return failed_observation(self.errors[arm])
        if self.hashes[arm].get(case.id) != request_hash:
            return failed_observation(
                "Fixture is stale for the model-visible request. Review new evidence."
            )
        rows = self.recordings[arm].get(case.id, [])
        if not isinstance(rows, list) or len(rows) != planned:
            return failed_observation(
                f"Expected {planned} frozen observation(s) for {case.id}/{arm}."
            )
        try:
            row = Fixture.model_validate(rows[index - 1])
        except ValueError:
            return failed_observation(f"Invalid frozen observation at {case.id}/{arm}/{index}.")
        return Observation(
            **row.model_dump(),
            status="ok",
            error=None,
            model=MODEL,
            request_id=None,
            stop_reason="end_turn",
            cost_usd=format(cost_usd(row.input_tokens, row.output_tokens), "f"),
        )


def run_comparison(
    root: Path,
    *,
    mode: Mode = "fixture",
    scenario: Scenario = "repaired",
    reference: Path | None = None,
    case_ids: list[str] | None = None,
    trials: int | None = None,
    budget: Decimal | None = None,
    title: str | None = None,
) -> Comparison:
    reference = reference or root
    all_cases = load_cases(root / "corpus/cases.json")
    cases = all_cases
    if case_ids:
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("Case selection contains a duplicate ID")
        if unknown := set(case_ids).difference(case.id for case in all_cases):
            raise ValueError(f"Unknown case IDs: {sorted(unknown)}")
        cases = [case for case in all_cases if case.id in case_ids]
    planned = trials if trials is not None else (1 if mode == "fixture" else 3)
    if not 1 <= planned <= 20:
        raise ValueError("Trials per arm must be between 1 and 20")
    if mode == "live" and (scenario != "repaired" or budget is None):
        raise ValueError(
            "Live mode requires an explicit USD budget and uses the checked-out prompts"
        )
    if mode == "fixture" and budget is not None:
        raise ValueError("Only live comparisons accept a paid budget")

    prompts = read_prompts(root, reference)
    request_hashes = request_manifest(cases, prompts)
    revised_labels: set[str] = set()
    if reference.resolve() != root.resolve():
        baseline_cases = {c.id: c for c in load_cases(reference / "corpus/cases.json")}
        revised_labels = {
            c.id
            for c in cases
            if c.id not in baseline_cases
            or c.expected != baseline_cases[c.id].expected
            or c.cluster != baseline_cases[c.id].cluster
        }
    started = datetime.now(UTC) if mode == "live" else None
    fixtures = (
        Fixtures(root, reference, scenario, {c.id for c in all_cases})
        if mode == "fixture"
        else None
    )
    live = None
    setup_error = None
    if mode == "live":
        try:
            live = LiveProvider(budget_usd=budget)
        except ValueError as error:
            setup_error = str(error)

    rows = []
    try:
        for position, case in enumerate(cases):
            observed: dict[Arm, list[Trial]] = {"baseline": [], "candidate": []}
            for index in range(1, planned + 1):
                # Matched trials alternate which arm goes first both within and across cases.
                order = ARMS if (position + index) % 2 else ARMS[::-1]
                for arm in order:
                    if case.id in revised_labels:
                        record = failed_observation(
                            "Benchmark label or family differs from the reference revision."
                        )
                    elif fixtures is not None:
                        record = fixtures.observe(
                            case, arm, index, planned, request_hashes[arm][case.id]
                        )
                    elif live is not None:
                        record = Observation.model_validate(live.collect(case, prompts[arm]))
                    else:
                        record = failed_observation(
                            setup_error or "Provider unavailable", known_cost="0"
                        )
                    observed[arm].append(evaluate(case, record, index))
            rows.append(
                CaseResult(
                    **case.model_dump(),
                    **observed,
                    request_hashes={arm: request_hashes[arm][case.id] for arm in ARMS},
                )
            )
    finally:
        if live is not None:
            live.close()

    policy = Policy(max_accuracy_drop=0.0 if mode == "fixture" else 0.05)
    if title is None:
        if mode == "live":
            title = "Live prompt comparison"
        elif scenario == "regressed":
            title = "Guardrail regression"
        else:
            title = "Candidate comparison"
    module_dir = Path(__file__).parent
    provenance = {
        "model": MODEL,
        "sdk_version": version("anthropic"),
        "baseline_revision": source_revision(reference),
        "candidate_revision": source_revision(root),
        "baseline_inputs_dirty": has_input_changes(reference),
        "candidate_inputs_dirty": has_input_changes(root),
        "instrument_sha256": digest(
            {
                name: sha256((module_dir / name).read_bytes()).hexdigest()
                for name in (
                    "domain.py",
                    "grading.py",
                    "guardrails.py",
                    "provider.py",
                    "evidence.py",
                    "metrics.py",
                    "runner.py",
                )
            }
        ),
        "prompt_sha256": {
            arm: sha256(prompt.encode()).hexdigest() for arm, prompt in prompts.items()
        },
        "corpus_sha256": digest([case.model_dump(mode="json") for case in all_cases]),
        "dependency_lock_sha256": sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "scenario": scenario if fixtures else "live",
        "started_at": started.isoformat() if started else None,
        "finished_at": datetime.now(UTC).isoformat() if started else None,
        "order": "alternating paired arms; first arm alternates per case and repetition",
        "rate_card": {
            "model": MODEL,
            "currency": "USD",
            "per_million_input": str(RATE_INPUT),
            "per_million_output": str(RATE_OUTPUT),
            "source": "https://platform.claude.com/docs/en/about-claude/pricing",
            "as_of": "2026-11-20",
            "tier": "standard, global, uncached",
            "input_tier_break": 100_000,
            "long_input_multiplier": 5,
        },
        "budget_usd": str(budget) if budget is not None else None,
        "measurement": "hand-authored synthetic responses, token counts and timings"
        if fixtures
        else "Anthropic Messages, sequential, preflight plus completion wall time, provider-reported tokens",
    }
    artifact = Comparison(
        id="pending",
        title=title,
        mode=mode,
        provenance=provenance,
        policy=policy,
        trials_per_case=planned,
        cases=rows,
        summary=summarize(rows, policy),
    )
    artifact.id = artifact_id(artifact.model_dump(mode="json"))
    return artifact


def verify(artifact: Comparison) -> None:
    """Verify content identity and regrade every arm using only embedded observations."""
    if artifact.id != artifact_id(artifact.model_dump(mode="json")):
        raise ValueError("Artifact content hash differs from its comparison ID")
    rates = artifact.provenance["rate_card"]
    input_rate = money(rates["per_million_input"])
    output_rate = money(rates["per_million_output"])
    fields = set(Observation.model_fields)
    for case in artifact.cases:
        for arm in ARMS:
            for trial in getattr(case, arm):
                if (
                    trial.status != "error"
                    and trial.cost_usd is not None
                    and trial.input_tokens is not None
                    and trial.output_tokens is not None
                ):
                    multiplier = (
                        rates["long_input_multiplier"]
                        if trial.input_tokens > rates["input_tier_break"]
                        else 1
                    )
                    charge = (
                        (trial.input_tokens * input_rate + trial.output_tokens * output_rate)
                        * multiplier
                        / 1_000_000
                    )
                    if charge != money(trial.cost_usd):
                        raise ValueError(
                            f"Recorded cost differs from token usage at {case.id}/{arm}/{trial.index}"
                        )
                observed = Observation.model_validate(trial.model_dump(include=fields))
                if evaluate(case, observed, trial.index) != trial:
                    raise ValueError(f"Replayed grading differs at {case.id}/{arm}/{trial.index}")
    if summarize(artifact.cases, artifact.policy) != artifact.summary:
        raise ValueError("Replayed verdict or metrics differ from the recorded summary")


def read_artifact(path: Path) -> Comparison:
    artifact = Comparison.model_validate_json(path.read_bytes())
    verify(artifact)
    return artifact
