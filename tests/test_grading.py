import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evalsuite.domain import Case, load_cases
from evalsuite.grading import ASSERTION_NAMES, grade, parse_response

ROOT = Path(__file__).resolve().parents[1]
ABSTENTION = "I can't confirm that from the available policy."


@pytest.fixture(scope="module")
def cases() -> dict[str, Case]:
    return {case.id: case for case in load_cases(ROOT / "corpus" / "cases.json")}


def _recordings(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _failures(case: Case, raw_response: str) -> set[str]:
    assertions = grade(case, raw_response)
    assert tuple(assertion.name for assertion in assertions) == ASSERTION_NAMES
    return {assertion.name for assertion in assertions if not assertion.passed}


def test_corpus_cases_are_portable_and_answers_are_visible(cases):
    assert len(cases) >= 36
    assert len({case.cluster for case in cases.values()}) >= 8
    for prompt in ("baseline", "candidate"):
        assert ABSTENTION in (ROOT / "prompts" / f"{prompt}.md").read_text(encoding="utf-8")

    for case in cases.values():
        assert Case.model_validate(case.model_dump(mode="json")) == case
        expected = case.expected
        evidence = [
            passage.text for passage in case.passages if passage.id in expected.evidence_ids
        ]
        if expected.decision == "abstain":
            assert expected.customer_response == ABSTENTION, case.id
        else:
            assert any(f'"{expected.customer_response}"' in quote for quote in evidence), case.id
        if expected.decision == "answer":
            assert any(f"value {expected.value}" in quote for quote in evidence), case.id


def test_case_rejects_evidence_outside_the_passages(cases):
    payload = cases["CMP-02"].model_dump(mode="json")
    payload["expected"]["evidence_ids"] = ["unavailable-policy"]
    with pytest.raises(ValidationError, match="unavailable evidence"):
        Case.model_validate(payload)


def test_baseline_is_a_passing_reference_and_candidate_covers_the_same_cases(cases):
    baseline = _recordings("baseline")
    candidate = _recordings("candidate")
    assert set(baseline) == set(candidate) == set(cases)

    for case_id, case in cases.items():
        assert len(baseline[case_id]) == len(candidate[case_id]) >= 1
        for trial in baseline[case_id]:
            assert not _failures(case, trial["text"]), case_id
            assert parse_response(trial["text"])


def test_grade_compares_against_the_case_and_leaves_a_recorded_output_frozen(cases):
    raw_response = _recordings("baseline")["CMP-02"][0]["text"]
    revised = cases["CMP-02"].model_dump(mode="json")
    revised["expected"]["evidence_ids"] = ["cmp-cart"]

    assert _failures(Case.model_validate(revised), raw_response) == {"attribution"}
    assert not _failures(cases["CMP-02"], raw_response)


@pytest.mark.parametrize(
    ("case_id", "failed_assertions"),
    [
        ("RET-04", {"value", "attribution", "grounded_response"}),
        ("SHP-03", {"decision", "value", "attribution", "grounded_response"}),
        ("BIL-02", {"attribution"}),
        ("BIL-03", {"decision", "value", "attribution", "grounded_response"}),
        ("BIL-04", {"grounded_response"}),
        ("REP-02", set(ASSERTION_NAMES)),
        ("CMP-02", {"attribution", "grounded_response"}),
        ("PCK-04", {"value", "attribution", "grounded_response"}),
    ],
)
def test_regressions_identify_independent_failure_dimensions(cases, case_id, failed_assertions):
    recording = _recordings("regressed")[case_id][0]
    assert _failures(cases[case_id], recording["text"]) == failed_assertions


@pytest.mark.parametrize(
    "invalid",
    [
        "```json\n{}\n```",
        "{} extra text",
        "[]",
        "null",
        '{"decision":"answer","decision":"answer","value":null,"evidence_ids":[],"customer_response":""}',
        '{"decision":"abstain","value":NaN,"evidence_ids":[],"customer_response":"Reply"}',
        '{"decision":"answer","value":5,"evidence_ids":[],"customer_response":"Reply"}',
        '{"decision":"answer","value":"5","evidence_ids":[1],"customer_response":"Reply"}',
        '{"decision":"unsure","value":null,"evidence_ids":[],"customer_response":"Reply"}',
        '{"decision":"answer","value":"5","evidence_ids":"source","customer_response":"Reply"}',
        '{"decision":"abstain","value":null,"evidence_ids":[],"customer_response":"Reply","extra":1}',
        '{"decision":"abstain","evidence_ids":[],"customer_response":"Reply"}',
        "[" * 2_000 + "]" * 2_000,
    ],
)
def test_schema_rejects_malformed_or_nonconforming_json(cases, invalid):
    assert _failures(cases["CMP-02"], invalid) == set(ASSERTION_NAMES)
    with pytest.raises(ValueError, match="Invalid JSON|Invalid response shape"):
        parse_response(invalid)


def test_evidence_accepts_different_order_but_rejects_duplicates(cases):
    case = cases["BIL-02"]
    raw = _recordings("baseline")[case.id][0]["text"]
    assert not _failures(case, "\n " + raw + " \n")

    value = json.loads(raw)
    value["evidence_ids"].append("bil-roles")
    duplicate = json.dumps(value)
    assert _failures(case, duplicate) == {"schema", "attribution"}
    with pytest.raises(ValueError, match="unique"):
        parse_response(duplicate)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"value": None}, "nonempty value and supporting evidence"),
        ({"value": "  "}, "nonempty value and supporting evidence"),
        ({"evidence_ids": []}, "nonempty value and supporting evidence"),
        ({"decision": "abstain"}, "require a null value"),
        ({"decision": "clarify", "value": None, "evidence_ids": []}, "supporting evidence"),
        ({"customer_response": " "}, "cannot be whitespace"),
    ],
)
def test_serving_parser_rejects_incoherent_response_without_a_case(update, message):
    response = {
        "decision": "answer",
        "value": "PLATE_C_SUPPORTED",
        "evidence_ids": ["cmp-mount"],
        "customer_response": "Your Desk Display 18 supports the Plate C tabletop mount.",
    }
    response.update(update)
    with pytest.raises(ValueError, match=message):
        parse_response(json.dumps(response))
