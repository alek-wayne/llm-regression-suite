"""Paired case means, descriptive uncertainty and a transparent engineering gate."""

import math
import random
from collections import defaultdict
from decimal import Decimal
from statistics import fmean

from .evidence import ARMS, Arm, CaseResult, Policy, Summary, Trial, money


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def cluster_interval(pairs: list[tuple[str, float]]) -> list[float] | None:
    """Resample entire policy families. Preserve case weighting within each draw."""
    groups: dict[str, list[float]] = defaultdict(list)
    for family, delta in pairs:
        groups[family].append(delta)
    if len(groups) < 2:
        return None
    clusters = [(sum(values), len(values)) for values in groups.values()]
    rng = random.Random(131)
    samples = []
    for _ in range(2000):
        draw = rng.choices(clusters, k=len(clusters))
        samples.append(sum(total for total, _ in draw) / sum(size for _, size in draw))
    return [round(percentile(samples, q), 8) for q in (0.025, 0.975)]


def arm_metrics(cases: list[CaseResult], arm: Arm) -> dict:
    groups: list[list[Trial]] = [getattr(case, arm) for case in cases]
    trials = [trial for group in groups for trial in group]
    delivered = [t for t in trials if t.status != "error"]
    measured = [t.latency_ms for t in delivered if t.latency_ms is not None]
    costs = [money(t.cost_usd) for t in trials if t.cost_usd is not None]
    checks: dict[str, int] = defaultdict(int)
    for t in delivered:
        for assertion in t.assertions:
            checks[assertion.name] += int(assertion.passed)
    complete = len(trials) == len(delivered)
    return {
        "scheduled": len(trials),
        "delivered": len(delivered),
        "errors": len(trials) - len(delivered),
        "invalid": sum(t.status == "invalid" for t in trials),
        "passed": sum(t.passed for t in trials),
        "accuracy": fmean(fmean(t.passed for t in group) for group in groups) if complete else None,
        "all_trials_passed_cases": sum(all(t.passed for t in group) for group in groups),
        "assertions_passed": dict(sorted(checks.items())),
        "withheld": sum(t.guardrail != "served" for t in delivered),
        "latency_samples": len(measured),
        "p50_ms": percentile(measured, 0.50),
        "p95_ms": percentile(measured, 0.95),
        "missing_token_counts": sum(
            t.input_tokens is None or t.output_tokens is None for t in delivered
        ),
        "input_tokens": sum(t.input_tokens for t in trials if t.input_tokens is not None),
        "output_tokens": sum(t.output_tokens for t in trials if t.output_tokens is not None),
        "known_cost_usd": format(sum(costs, Decimal("0.00000000")), "f"),
        "unknown_cost_trials": len(trials) - len(costs),
        "max_cost_usd": format(max(costs), "f") if costs else None,
    }


def summarize(cases: list[CaseResult], policy: Policy) -> Summary:
    measures = {arm: arm_metrics(cases, arm) for arm in ARMS}
    baseline, candidate = (measures[arm] for arm in ARMS)
    pairs, losses, wins = [], [], []
    for case in cases:
        if any(t.status == "error" for t in (*case.baseline, *case.candidate)):
            continue
        change = fmean(t.passed for t in case.candidate) - fmean(t.passed for t in case.baseline)
        pairs.append((case.cluster, change))
        if change < 0:
            losses.append(case.id)
        elif change > 0:
            wins.append(case.id)

    delta = round(fmean(change for _, change in pairs), 10) if len(pairs) == len(cases) else None
    missing = []
    for arm, row in measures.items():
        if row["errors"]:
            missing.append(f"{arm.capitalize()} had {row['errors']} incomplete trial(s).")
        if row["unknown_cost_trials"]:
            missing.append(
                f"{arm.capitalize()} is missing cost for {row['unknown_cost_trials']} trial(s)."
            )
        if row["missing_token_counts"]:
            missing.append(f"{arm.capitalize()} is missing delivered token usage.")
        if row["latency_samples"] < row["delivered"]:
            missing.append(f"{arm.capitalize()} is missing delivered response timing.")
    failures = []
    if candidate["accuracy"] is not None and candidate["accuracy"] < policy.min_accuracy:
        failures.append(
            f"Candidate accuracy {candidate['accuracy']:.1%} fell below {policy.min_accuracy:.1%}."
        )
    if delta is not None and delta < -policy.max_accuracy_drop:
        failures.append(
            f"Accuracy dropped {-delta * 100:.2f} pp, exceeding the {100 * policy.max_accuracy_drop:g} pp allowance."
        )
    if candidate["p95_ms"] is not None and candidate["p95_ms"] > policy.max_p95_ms:
        failures.append(f"Candidate p95 exceeded {policy.max_p95_ms:g} ms.")
    if candidate["max_cost_usd"] and money(candidate["max_cost_usd"]) > money(policy.max_trial_usd):
        failures.append(f"A candidate trial exceeded the ${policy.max_trial_usd} cost limit.")
    verdict = "INCONCLUSIVE" if missing else "FAIL" if failures else "PASS"
    return Summary(
        verdict=verdict,
        reasons=missing + failures or ["All configured quality, latency and cost limits passed."],
        case_count=len(cases),
        cluster_count=len({c.cluster for c in cases}),
        baseline_accuracy=baseline["accuracy"],
        candidate_accuracy=candidate["accuracy"],
        delta=delta,
        ci95=cluster_interval(pairs) if delta is not None else None,
        baseline_p95_ms=baseline["p95_ms"],
        candidate_p95_ms=candidate["p95_ms"],
        baseline_cost_usd=baseline["known_cost_usd"],
        candidate_cost_usd=candidate["known_cost_usd"],
        arms=measures,
        losses=losses,
        wins=wins,
    )
