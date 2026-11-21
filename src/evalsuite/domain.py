from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Passage(StrictModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class Response(StrictModel):
    decision: Literal["answer", "clarify", "abstain"]
    value: str | None
    evidence_ids: list[str]
    customer_response: str = Field(min_length=1)


class Case(StrictModel):
    id: str = Field(min_length=1)
    cluster: str = Field(min_length=1)
    category: str = Field(min_length=1)
    question: str = Field(min_length=1)
    passages: list[Passage] = Field(min_length=1)
    expected: Response

    @model_validator(mode="after")
    def check_answer_key(self) -> "Case":
        passage_ids = [passage.id for passage in self.passages]
        if len(passage_ids) != len(set(passage_ids)):
            raise ValueError(f"{self.id} contains duplicate passages")

        expected_ids = self.expected.evidence_ids
        if len(expected_ids) != len(set(expected_ids)):
            raise ValueError(f"{self.id} contains duplicate expected evidence IDs")
        if missing_ids := set(expected_ids).difference(passage_ids):
            raise ValueError(f"{self.id} expects unavailable evidence {sorted(missing_ids)}")

        if self.expected.decision == "answer":
            if not self.expected.value or not expected_ids:
                raise ValueError(f"{self.id} requires a nonempty answer and evidence")
        elif self.expected.value is not None:
            raise ValueError(f"{self.id} must use null outside of the answer decision")
        return self


class _CatalogCase(StrictModel):
    id: str
    cluster: str
    category: str
    question: str
    passage_ids: list[str]
    expected: Response


class _Catalog(StrictModel):
    passages: dict[str, str]
    cases: list[_CatalogCase] = Field(min_length=1)


def load_cases(path: Path) -> list[Case]:
    """Resolve the shared passage catalog into portable evaluation cases."""
    catalog = _Catalog.model_validate_json(path.read_text(encoding="utf-8"))
    cases: list[Case] = []
    seen: set[str] = set()

    for entry in catalog.cases:
        if entry.id in seen:
            raise ValueError(f"Duplicate case ID {entry.id!r}")
        seen.add(entry.id)

        try:
            passages = [
                Passage(id=passage_id, text=catalog.passages[passage_id])
                for passage_id in entry.passage_ids
            ]
        except KeyError as exc:
            raise ValueError(f"{entry.id} references unknown passage {exc.args[0]!r}") from exc

        cases.append(
            Case(
                id=entry.id,
                cluster=entry.cluster,
                category=entry.category,
                question=entry.question,
                passages=passages,
                expected=entry.expected,
            )
        )
    return cases
