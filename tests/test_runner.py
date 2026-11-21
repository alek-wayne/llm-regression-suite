import json
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from evalsuite.cli import main
from evalsuite.evidence import Comparison, Policy, artifact_id
from evalsuite.metrics import cluster_interval, percentile, summarize
from evalsuite.reporting import publish_report
from evalsuite.runner import read_artifact, run_comparison, verify

ROOT = Path(__file__).resolve().parents[1]


def test_offline_gate_and_replay_are_reproducible_without_a_provider(
    project, monkeypatch, tmp_path
):
    def unexpected_client(*args, **kwargs):
        pytest.fail("Offline runs must not construct an Anthropic client")

    monkeypatch.setattr("evalsuite.runner.LiveProvider", unexpected_client)
    monkeypatch.chdir(project)
    for scenario, code, passes in (("regressed", 1, 40), ("repaired", 0, 48)):
        out = tmp_path / "reports" / scenario
        assert main(["compare", "--scenario", scenario, "--out", str(out)]) == code
        report = read_artifact(out / "comparison.json")
        assert report.summary.arms["baseline"]["passed"] == 48
        assert report.summary.arms["candidate"]["passed"] == passes
        assert len(report.summary.losses) == 48 - passes
        assert main(["replay", str(out / "comparison.json")]) == code
        assert report == run_comparison(project, scenario=scenario)
        assert report.id in (out / "report.md").read_text()


def test_cli_evaluates_changed_candidate_recording_without_changing_baseline(project):
    case_id = "BIL-04"
    path = project / "fixtures/candidate.json"
    recs = json.loads(path.read_text())
    recs[case_id] = json.loads((project / "fixtures/regressed.json").read_text())[case_id]
    path.write_text(json.dumps(recs))
    artifact = run_comparison(project, reference=ROOT)
    assert artifact.summary.verdict == "FAIL"
    assert artifact.summary.losses == [case_id]
    assert artifact.summary.candidate_accuracy == pytest.approx(47 / 48)
    case = next(c for c in artifact.cases if c.id == case_id)
    assert case.candidate[0].guardrail == "withheld_unsupported_wording"


def test_prompt_change_invalidates_recordings_and_preserves_baseline(project):
    path = project / "prompts/candidate.md"
    path.write_text(path.read_text() + "\nPrefer the standard policy in every case.")
    artifact = run_comparison(project, reference=ROOT, case_ids=["RET-04"])
    assert artifact.summary.verdict == "INCONCLUSIVE"
    assert artifact.summary.delta is None
    assert artifact.summary.baseline_accuracy == 1
    assert artifact.summary.candidate_accuracy is None
    assert "stale" in artifact.cases[0].candidate[0].error
    assert artifact.cases[0].candidate[0].cost_usd is None


@pytest.mark.parametrize("mutation", ["missing", "extra", "invalid", "wrong_count", "missing_lock"])
def test_incomplete_evidence_blocks_the_gate(project, mutation):
    path = project / "fixtures/candidate.json"
    records = json.loads(path.read_text())
    if mutation == "missing":
        del records["CMP-02"]
    elif mutation == "extra":
        records["obsolete-case"] = records["CMP-02"]
    elif mutation == "invalid":
        records["CMP-02"][0]["latency_ms"] = -1
    elif mutation == "wrong_count":
        records["CMP-02"].append(records["CMP-02"][0])
    else:
        (project / "fixtures/requests.lock.json").unlink()
    path.write_text(json.dumps(records))
    artifact = run_comparison(project, reference=ROOT, case_ids=["CMP-02"])
    assert artifact.summary.verdict == "INCONCLUSIVE"
    assert artifact.summary.delta is None
    assert artifact.summary.arms["candidate"]["errors"] == 1
    verify(artifact)


def test_replay_detects_tampering_including_relabelled_derived_values(tmp_path):
    artifact = run_comparison(ROOT, case_ids=["CMP-02"], scenario="regressed")
    publish_report(artifact, tmp_path)
    document = json.loads((tmp_path / "comparison.json").read_text())
    document["cases"][0]["candidate"][0]["passed"] = True
    with pytest.raises(ValueError, match="content hash"):
        verify(Comparison.model_validate(document))
    document["id"] = artifact_id(document)
    with pytest.raises(ValueError, match="Replayed grading"):
        verify(Comparison.model_validate(document))
    document["cases"][0]["candidate"][0]["index"] = 2
    with pytest.raises(ValueError, match="Unpaired"):
        Comparison.model_validate(document)


def test_gate_checks_absolute_accuracy_latency_and_cost_independently(project):
    artifact = run_comparison(project, case_ids=["BIL-04"])
    case = artifact.cases[0].model_copy(deep=True)
    case.candidate[0].latency_ms = 5001
    case.candidate[0].cost_usd = "0.01000001"
    s = summarize([case], Policy())
    assert s.verdict == "FAIL"
    assert s.delta == 0
    assert len(s.reasons) == 2
    assert "p95" in s.reasons[0] and "cost" in s.reasons[1]
    case.candidate[0].latency_ms = 5000
    case.candidate[0].cost_usd = "0.01000000"
    assert summarize([case], Policy()).verdict == "PASS"


@pytest.mark.parametrize(
    ("baseline_scores", "candidate_scores", "expected_accuracy", "verdict"),
    [
        ((19, 17), (19, 17), 0.90, "PASS"),
        ((19, 17), (18, 17), 0.875, "FAIL"),
        ((20, 20), (19, 19), 0.95, "PASS"),
        ((20, 20), (19, 18), 0.925, "FAIL"),
    ],
)
def test_accuracy_and_drop_thresholds_are_inclusive(
    baseline_scores, candidate_scores, expected_accuracy, verdict
):
    cases = run_comparison(ROOT, case_ids=["BIL-04", "CMP-02"]).cases
    for case, baseline, candidate in zip(cases, baseline_scores, candidate_scores, strict=True):
        for arm, passes in (("baseline", baseline), ("candidate", candidate)):
            trial = getattr(case, arm)[0]
            scored = [
                trial.model_copy(update={"index": i, "passed": i <= passes}) for i in range(1, 21)
            ]
            setattr(case, arm, scored)

    summary = summarize(cases, Policy(min_accuracy=0.90, max_accuracy_drop=0.05))
    assert summary.candidate_accuracy == expected_accuracy
    assert summary.verdict == verdict


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("stop_reason", "max_tokens", "end_turn"),
        ("stop_reason", "refusal", "end_turn"),
        ("stop_reason", None, "end_turn"),
        ("model", "different-model", "model"),
    ],
)
def test_replay_rejects_contradictory_delivery_evidence(tmp_path, field, value, message):
    document = run_comparison(ROOT, case_ids=["CMP-02"]).model_dump(mode="json")
    document["cases"][0]["candidate"][0][field] = value
    document["id"] = artifact_id(document)
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match=message):
        read_artifact(path)


def test_invalid_rate_card_exits_inconclusive_without_repricing(tmp_path, capsys):
    document = run_comparison(ROOT, case_ids=["CMP-02"]).model_dump(mode="json")
    document["provenance"]["rate_card"] = []
    document["id"] = artifact_id(document)
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(document))
    assert main(["replay", str(path)]) == 2
    assert "Evaluation unavailable" in capsys.readouterr().err


def test_replay_preserves_legacy_multitrial_accuracy(project, tmp_path):
    for arm in ("baseline", "candidate"):
        path = project / f"fixtures/{arm}.json"
        records = json.loads(path.read_text())
        for case_id in ("CMP-01", "CMP-02"):
            records[case_id] = [dict(records[case_id][0]) for _ in range(6)]
        records["CMP-01"][0]["text"] = "{}"
        path.write_text(json.dumps(records))
    artifact = run_comparison(project, case_ids=["CMP-01", "CMP-02"], trials=6)
    assert artifact.summary.candidate_accuracy == 11 / 12
    document = artifact.model_dump(mode="json")
    del document["provenance"]["metrics_version"]
    # Version 1 averaged 5/6 and 6/6 as floats before serializing these fields.
    legacy_accuracy = 0.9166666666666667
    for arm in ("baseline", "candidate"):
        document["summary"][f"{arm}_accuracy"] = legacy_accuracy
        document["summary"]["arms"][arm]["accuracy"] = legacy_accuracy
    document["id"] = artifact_id(document)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(document))
    assert read_artifact(path).summary.verdict == "PASS"
    document["summary"]["arms"]["candidate"]["passed"] = 12
    document["id"] = artifact_id(document)
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="Replayed verdict or metrics"):
        read_artifact(path)


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_frozen_output_is_a_replayable_quality_failure(project, tmp_path, text):
    path = project / "fixtures/candidate.json"
    records = json.loads(path.read_text())
    records["CMP-02"][0]["text"] = text
    path.write_text(json.dumps(records))
    artifact = run_comparison(project, case_ids=["CMP-02"])
    trial = artifact.cases[0].candidate[0]
    assert trial.status == "invalid" and trial.cost_usd is not None
    assert artifact.summary.verdict == "FAIL"
    publish_report(artifact, tmp_path)
    assert main(["replay", str(tmp_path / "comparison.json")]) == 1


def test_live_validates_required_inputs_before_dispatch(project, monkeypatch):
    (project / "uv.lock").unlink()

    def unexpected_client(*args, **kwargs):
        pytest.fail("No client should be created for an incomplete checkout")

    monkeypatch.setattr("evalsuite.runner.LiveProvider", unexpected_client)
    with pytest.raises(FileNotFoundError, match="uv.lock"):
        run_comparison(project, mode="live", case_ids=["CMP-02"], budget=Decimal("0.01"))


def test_live_schedule_counterbalances_order_and_records_every_trial(monkeypatch):
    calls = []
    baseline = (ROOT / "prompts/baseline.md").read_text()
    recordings = json.loads((ROOT / "fixtures/baseline.json").read_text())

    class Stub:
        def __init__(self, budget_usd):
            assert budget_usd == Decimal("0.01")

        def collect(self, case, prompt):
            arm = "baseline" if prompt == baseline else "candidate"
            calls.append((case.id, arm))
            row = recordings[case.id][0]
            return {
                "text": row["text"],
                "status": "ok",
                "error": None,
                "input_tokens": 100,
                "output_tokens": 20,
                "latency_ms": 100.0,
                "cost_usd": "0.00002",
                "model": "claude-haiku-5-5",
                "request_id": "mock",
                "stop_reason": "end_turn",
            }

        def close(self):
            calls.append(("closed", ""))

    monkeypatch.setattr("evalsuite.runner.LiveProvider", Stub)
    artifact = run_comparison(
        ROOT, mode="live", case_ids=["BIL-04", "CMP-02"], trials=2, budget=Decimal("0.01")
    )
    assert [arm for _, arm in calls[:-1]] == [
        "baseline",
        "candidate",
        "candidate",
        "baseline",
        "candidate",
        "baseline",
        "baseline",
        "candidate",
    ]
    assert calls[-1] == ("closed", "")
    assert artifact.summary.verdict == "PASS"
    assert artifact.summary.arms["candidate"]["scheduled"] == 4
    verify(artifact)


def test_missing_credentials_block_comparison_with_zero_spend(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    artifact = run_comparison(
        ROOT, mode="live", case_ids=["CMP-02"], trials=1, budget=Decimal("0.05")
    )
    assert artifact.summary.verdict == "INCONCLUSIVE"
    assert artifact.summary.arms["candidate"]["unknown_cost_trials"] == 0
    assert artifact.summary.candidate_cost_usd == "0.00000000"
    assert artifact.cases[0].candidate[0].request_id is None


@given(st.lists(st.integers(min_value=0, max_value=50_000), min_size=1, max_size=40))
def test_percentiles_preserve_bounds_and_order(values):
    actual = percentile(values, 0.95)
    assert min(values) <= percentile(values, 0.5) <= actual <= max(values)
    assert actual == percentile(list(reversed(values)), 0.95)


def test_small_sample_percentile_and_clustered_interval():
    assert percentile([100.0, 300.0], 0.95) == 290
    assert percentile([], 0.95) is None
    assert cluster_interval([("family", 1.0), ("family", -1.0)]) is None
    assert cluster_interval([("a", 0.25), ("b", 0.25)]) == [0.25, 0.25]


def test_reference_prevents_silent_gold_label_changes(project):
    path = project / "corpus/cases.json"
    catalog = json.loads(path.read_text())
    case = next(row for row in catalog["cases"] if row["id"] == "CMP-02")
    case["expected"]["value"] = "WRONG_LABEL"
    path.write_text(json.dumps(catalog))
    artifact = run_comparison(project, reference=ROOT, case_ids=["CMP-02"])
    assert artifact.summary.verdict == "INCONCLUSIVE"
    assert "reference revision" in artifact.cases[0].candidate[0].error


def test_replay_prices_observed_tokens_using_embedded_rate_card():
    artifact = run_comparison(ROOT, case_ids=["CMP-02"])
    document = artifact.model_dump(mode="json")
    document["cases"][0]["candidate"][0]["cost_usd"] = "0.0099999"
    document["id"] = artifact_id(document)
    with pytest.raises(ValueError, match="differs from token usage"):
        verify(Comparison.model_validate(document))


def test_source_archive_can_run_without_git(project, monkeypatch):
    monkeypatch.setenv("PATH", "")
    artifact = run_comparison(project, case_ids=["CMP-02"])
    assert artifact.provenance["candidate_revision"] is None
    assert artifact.provenance["candidate_inputs_dirty"] is None
    assert artifact.summary.verdict == "PASS"
    verify(artifact)
