import json

from pydantic import ValidationError

from .domain import Case, Response, StrictModel

ASSERTION_NAMES = ("schema", "decision", "value", "attribution", "grounded_response")


class Assertion(StrictModel):
    name: str
    passed: bool
    detail: str


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(constant: str) -> None:
    raise ValueError(f"Invalid JSON constant {constant!r}")


def _schema_failure(detail: str) -> list[Assertion]:
    return [
        Assertion(name="schema", passed=False, detail=detail),
        *[
            Assertion(name=name, passed=False, detail="Requires a valid response object")
            for name in ASSERTION_NAMES[1:]
        ],
    ]


def _parse_wire(raw_text: str) -> Response:
    try:
        data = json.loads(
            raw_text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except RecursionError as exc:
        raise ValueError("Invalid JSON: nesting exceeds the parser limit") from exc
    except ValueError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    try:
        return Response.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        detail = ", ".join(
            f"{'.'.join(map(str, error['loc'])) or 'response'} ({error['type']})"
            for error in errors[:4]
        )
        if len(errors) > 4:
            detail += f", and {len(errors) - 4} more"
        raise ValueError(f"Invalid response shape: {detail}") from exc


def _validate_contract(response: Response) -> None:
    if not response.customer_response.strip():
        raise ValueError("The customer response cannot be whitespace")
    if len(set(response.evidence_ids)) != len(response.evidence_ids):
        raise ValueError("Evidence IDs must be unique")
    if response.decision == "answer":
        if not response.value or not response.value.strip() or not response.evidence_ids:
            raise ValueError("An answer requires a nonempty value and supporting evidence")
    elif response.value is not None:
        raise ValueError("Clarify and abstain decisions require a null value")
    if response.decision == "clarify" and not response.evidence_ids:
        raise ValueError("A clarification requires supporting evidence")


def parse_response(raw_text: str) -> Response:
    """Validate the response contract without consulting the case's answer key."""
    response = _parse_wire(raw_text)
    _validate_contract(response)
    return response


def grade(case: Case, raw_text: str) -> list[Assertion]:
    """Score structure, outcome, attribution, and approved wording independently."""
    try:
        actual = _parse_wire(raw_text)
    except ValueError as exc:
        return _schema_failure(str(exc))

    try:
        _validate_contract(actual)
    except ValueError as exc:
        schema = Assertion(name="schema", passed=False, detail=str(exc))
    else:
        schema = Assertion(name="schema", passed=True, detail="Valid response contract")

    expected = case.expected
    correct_decision = actual.decision == expected.decision
    correct_value = actual.value == expected.value
    actual_ids = set(actual.evidence_ids)
    expected_ids = set(expected.evidence_ids)
    no_duplicate_ids = len(actual_ids) == len(actual.evidence_ids)
    correct_attribution = actual_ids == expected_ids and no_duplicate_ids
    correct_wording = actual.customer_response == expected.customer_response

    attribution_detail: list[str] = []
    if missing_ids := expected_ids - actual_ids:
        attribution_detail.append(f"missing {sorted(missing_ids)}")
    if extra_ids := actual_ids - expected_ids:
        attribution_detail.append(f"unexpected {sorted(extra_ids)}")
    if not no_duplicate_ids:
        attribution_detail.append("duplicate IDs")

    return [
        schema,
        Assertion(
            name="decision",
            passed=correct_decision,
            detail=(
                "Expected decision"
                if correct_decision
                else f"Expected {expected.decision!r}, received {actual.decision!r}"
            ),
        ),
        Assertion(
            name="value",
            passed=correct_value,
            detail="Expected value" if correct_value else f"Expected {expected.value!r}",
        ),
        Assertion(
            name="attribution",
            passed=correct_attribution,
            detail="Exact supporting IDs" if correct_attribution else ", ".join(attribution_detail),
        ),
        Assertion(
            name="grounded_response",
            passed=correct_wording,
            detail=(
                "Matches approved customer wording"
                if correct_wording
                else f"Expected approved wording {expected.customer_response!r}"
            ),
        ),
    ]
