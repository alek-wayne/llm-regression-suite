import json
from functools import cache
from hashlib import sha256
from importlib.resources import files
from typing import Literal

from .domain import Passage
from .grading import parse_response

GuardrailResult = Literal[
    "served",
    "withheld_schema",
    "withheld_unknown_citation",
    "withheld_unsupported_wording",
]

_ABSTENTION = "I can't confirm that from the available policy."


@cache
def _load_approvals() -> frozenset[tuple[str, str, str, str]]:
    resource = files("evalsuite").joinpath("policy_approvals.json")
    registry = json.loads(resource.read_text(encoding="utf-8"))
    return frozenset(
        (passage_id, digest, decision, wording)
        for passage_id, revisions in registry.items()
        for digest, decisions in revisions.items()
        for decision, wordings in decisions.items()
        for wording in wordings
    )


def runtime_guard(passages: list[Passage], raw_text: str) -> GuardrailResult:
    """Allow reviewed wording from the exact retrieved passage revision."""
    try:
        response = parse_response(raw_text)
    except ValueError:
        return "withheld_schema"

    passage_text_by_id = {passage.id: passage.text for passage in passages}
    if any(passage_id not in passage_text_by_id for passage_id in response.evidence_ids):
        return "withheld_unknown_citation"

    if response.decision == "abstain":
        approved = response.customer_response == _ABSTENTION
    else:
        approvals = _load_approvals()
        approved = any(
            (
                passage_id,
                sha256(passage_text_by_id[passage_id].encode("utf-8")).hexdigest(),
                response.decision,
                response.customer_response,
            )
            in approvals
            for passage_id in response.evidence_ids
        )
    return "served" if approved else "withheld_unsupported_wording"
