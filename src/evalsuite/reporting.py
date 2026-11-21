"""Atomic artifact writes and CI reports from one comparison object."""

import html
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .evidence import ARMS, Comparison


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as temp:
        name = temp.name
        try:
            temp.write(data)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
    try:
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n")


def index_entry(artifact: Comparison) -> dict:
    return {
        "id": artifact.id,
        "title": artifact.title,
        "mode": artifact.mode,
        "verdict": artifact.summary.verdict,
        "delta": artifact.summary.delta,
        "case_count": artifact.summary.case_count,
        "artifact": artifact.id + ".json",
    }


def markdown(artifact: Comparison) -> str:
    s = artifact.summary
    pp = "unavailable" if s.delta is None else f"{s.delta * 100:+.2f} pp"
    lines = [
        f"# {s.verdict} · {artifact.title}",
        "",
        (
            f"**{artifact.mode.upper()}** · {s.case_count} cases · {s.cluster_count} policy families · "
            f"{artifact.trials_per_case} trial(s) per case per arm · **{pp}**"
        ),
        "",
        artifact.provenance["measurement"].capitalize() + ".",
        "",
        *[f"- {reason}" for reason in s.reasons],
        "",
        "| Measure | Baseline | Candidate |",
        "| --- | ---: | ---: |",
    ]
    metrics = s.arms

    def row(label, field, formatter=str):
        values = [metrics[arm][field] for arm in ARMS]
        cells = [formatter(v) if v is not None else "n/a" for v in values]
        lines.append(f"| {label} | {cells[0]} | {cells[1]} |")

    row(
        "Delivered / scheduled",
        "delivered",
        lambda v: f"{v} / {s.case_count * artifact.trials_per_case}",
    )
    row("Strict accuracy", "accuracy", lambda v: f"{v:.2%}")
    row("Cases passing every trial", "all_trials_passed_cases")
    row("Contract-valid responses", "assertions_passed", lambda v: v.get("schema", 0))
    row("Correct attribution", "assertions_passed", lambda v: v.get("attribution", 0))
    row("Approved customer wording", "assertions_passed", lambda v: v.get("grounded_response", 0))
    row("Latency p50 (ms)", "p50_ms", lambda v: f"{v:.1f}")
    row("Latency p95 (ms)", "p95_ms", lambda v: f"{v:.1f}")
    row("Input tokens", "input_tokens")
    row("Output tokens", "output_tokens")
    row("Known USD at list price", "known_cost_usd")
    row("Trials with unknown cost", "unknown_cost_trials")
    if s.ci95:
        lines += [
            "",
            (
                f"Paired 95% family-bootstrap interval for the observed change: "
                f"{s.ci95[0] * 100:+.2f} to {s.ci95[1] * 100:+.2f} pp."
            ),
        ]

    def safe(value: str) -> str:
        return html.escape(value).replace("|", "&#124;").replace("\n", " ").replace("`", "&#96;")

    troubled = [c for c in artifact.cases if any(not t.passed for t in c.candidate)]
    if troubled:
        lines += [
            "",
            "## Candidate cases to inspect",
            "",
            "| Case | Category | Trial | Finding |",
            "| --- | --- | ---: | --- |",
        ]
        for case in troubled:
            for t in case.candidate:
                if t.passed:
                    continue
                failed = ", ".join(a.name for a in t.assertions if not a.passed)
                finding = t.error or failed or t.guardrail
                lines.append(
                    f"| {safe(case.id)} | {safe(case.category)} | {t.index} | {safe(finding)} |"
                )
    lines += [
        "",
        f"Comparison `{artifact.id}`. Full answers, source passages, thresholds and trial evidence are in `comparison.json`.",
        "",
    ]
    return "\n".join(lines)


def publish_report(artifact: Comparison, directory: Path) -> None:
    write_json(directory / "comparison.json", artifact.model_dump(mode="json"))
    write_text(directory / "report.md", markdown(artifact))
