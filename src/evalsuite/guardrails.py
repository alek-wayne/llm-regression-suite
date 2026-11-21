import re
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
_APPROVALS = {
    "answer": re.compile(r'\bapproved reply: "([^"]+)"'),
    "clarify": re.compile(r'\bapproved question: "([^"]+)"'),
}


def runtime_guard(passages: list[Passage], raw_text: str) -> GuardrailResult:
    """Check whether the wording may be served using only the retrieved passages."""
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
        pattern = _APPROVALS[response.decision]
        approved = any(
            response.customer_response in pattern.findall(passage_text_by_id[passage_id])
            for passage_id in response.evidence_ids
        )
    return "served" if approved else "withheld_unsupported_wording"
