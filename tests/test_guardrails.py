import json
from pathlib import Path

import pytest

from evalsuite.domain import Passage, load_cases
from evalsuite.grading import grade
from evalsuite.guardrails import runtime_guard

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cases():
    return {case.id: case for case in load_cases(ROOT / "corpus" / "cases.json")}


def _recording(arm: str, case_id: str) -> str:
    fixture = json.loads((ROOT / "fixtures" / f"{arm}.json").read_text(encoding="utf-8"))
    return fixture[case_id][0]["text"]


def test_all_approved_baseline_recordings_can_be_served(cases):
    fixture = json.loads((ROOT / "fixtures/baseline.json").read_text(encoding="utf-8"))
    for case_id, case in cases.items():
        for attempt in fixture[case_id]:
            assert runtime_guard(case.passages, attempt["text"]) == "served", case_id


def test_unknown_citation_is_withheld_even_with_approved_wording(cases):
    case = cases["CMP-02"]
    response = json.loads(_recording("baseline", case.id))
    response["evidence_ids"].append("not-retrieved")
    assert runtime_guard(case.passages, json.dumps(response)) == "withheld_unknown_citation"


@pytest.mark.parametrize(
    ("case_id", "outcome"),
    [
        ("REP-02", "withheld_schema"),
        ("BIL-04", "withheld_unsupported_wording"),
        ("CMP-02", "withheld_unsupported_wording"),
    ],
)
def test_regression_recordings_with_malformed_or_unsupported_replies_are_withheld(
    cases, case_id, outcome
):
    assert runtime_guard(cases[case_id].passages, _recording("regressed", case_id)) == outcome


def test_approved_but_inapplicable_policy_wording_requires_offline_grading(cases):
    case = cases["RET-04"]
    wrong_scope = _recording("regressed", case.id)
    assert runtime_guard(case.passages, wrong_scope) == "served"
    assert {check.name for check in grade(case, wrong_scope) if not check.passed} == {
        "value",
        "attribution",
        "grounded_response",
    }


@pytest.mark.parametrize(
    "update",
    [
        {"customer_response": "Your Desk Display 18 supports the Plate C tabletop mount"},
        {"customer_response": "supports the Plate C tabletop mount."},
        {"customer_response": "Your Cart Display 18 supports the Plate C tabletop mount."},
        {"customer_response": "The Desk Display 18 accepts the Plate C tabletop mount."},
        {"decision": "clarify", "value": None},
    ],
)
def test_only_the_complete_approval_for_the_decision_and_citation_is_served(cases, update):
    case = cases["CMP-02"]
    response = json.loads(_recording("baseline", case.id))
    response.update(update)
    assert runtime_guard(case.passages, json.dumps(response)) == "withheld_unsupported_wording"


def test_abstention_uses_only_the_prompts_fixed_fallback(cases):
    case = cases["SHP-03"]
    response = json.loads(_recording("baseline", case.id))
    response["customer_response"] += " We can still guarantee next-day delivery."
    assert runtime_guard(case.passages, json.dumps(response)) == "withheld_unsupported_wording"


@pytest.mark.parametrize("label", ["Unapproved", "Not-approved", "Withdrawn approved"])
def test_unapproved_quotes_do_not_count_as_approved_wording(label):
    passage = Passage(id="policy", text=f'{label} reply: "Your refund is confirmed."')
    response = json.dumps(
        {
            "decision": "answer",
            "value": "REFUND_CONFIRMED",
            "evidence_ids": ["policy"],
            "customer_response": "Your refund is confirmed.",
        }
    )
    assert runtime_guard([passage], response) == "withheld_unsupported_wording"


@pytest.mark.parametrize(
    ("passage_id", "text_suffix", "outcome"),
    [
        ("cmp-mount", "", "served"),
        ("cmp-mount", " ", "withheld_unsupported_wording"),
        ("cmp-mount", " This approval has been withdrawn.", "withheld_unsupported_wording"),
        ("cmp-cart", "", "withheld_unsupported_wording"),
    ],
    ids=["approved-source", "altered-text", "withdrawn-approval", "different-source-id"],
)
def test_approval_requires_the_exact_source_identity_and_revision(
    cases, passage_id, text_suffix, outcome
):
    case = cases["CMP-02"]
    source = next(passage for passage in case.passages if passage.id == "cmp-mount")
    retrieved = Passage(id=passage_id, text=source.text + text_suffix)
    response = json.loads(_recording("baseline", case.id))
    response["evidence_ids"] = [passage_id]
    assert runtime_guard([retrieved], json.dumps(response)) == outcome


def test_contract_grading_keeps_value_and_citation_diagnostics(cases):
    case = cases["CMP-02"]
    response = json.loads(_recording("baseline", case.id))
    response["value"] = None
    checks = {a.name: a.passed for a in grade(case, json.dumps(response))}
    assert checks == {
        "schema": False,
        "decision": True,
        "value": False,
        "attribution": True,
        "grounded_response": True,
    }
