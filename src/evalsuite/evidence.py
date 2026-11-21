"""Versioned, portable evidence shared by the runner, database, CI, and console."""

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .domain import Case
from .grading import Assertion

Count = Annotated[int, Field(ge=0)]
Duration = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Arm = Literal["baseline", "candidate"]
ARMS: tuple[Arm, Arm] = ("baseline", "candidate")
Verdict = Literal["PASS", "FAIL", "INCONCLUSIVE"]
EXIT_CODE: dict[Verdict, int] = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Policy(Record):
    min_accuracy: float = Field(default=0.90, ge=0, le=1)
    max_accuracy_drop: float = Field(default=0.05, ge=0, le=1)
    max_p95_ms: float = Field(default=5000, gt=0)
    max_trial_usd: str = "0.01000000"

    @field_validator("max_trial_usd")
    @classmethod
    def positive_price(cls, value: str) -> str:
        if money(value) <= 0:
            raise ValueError("Cost limit must be positive")
        return value


class RateCard(BaseModel):
    """Fields needed to replay standard USD charges, including older rate cards."""

    model_config = ConfigDict(strict=True)
    model: str = Field(min_length=1)
    currency: Literal["USD"]
    per_million_input: str
    per_million_output: str
    input_tier_break: Count
    long_input_multiplier: int = Field(ge=1)


def money(value: str) -> Decimal:
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid decimal amount") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("Cost must be a finite, nonnegative amount")
    return amount


class Observation(Record):
    text: str
    status: Literal["ok", "invalid", "error"]
    error: str | None
    input_tokens: Count | None
    output_tokens: Count | None
    latency_ms: Duration | None
    cost_usd: str | None
    model: str
    request_id: str | None
    stop_reason: str | None
    # Provider-specific accounting stays attached to the original response.
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.cost_usd is not None:
            money(self.cost_usd)
        if self.status == "ok" and self.error is not None:
            raise ValueError("A successful delivery cannot have an error")
        if self.status == "ok" and self.stop_reason != "end_turn":
            raise ValueError("A completed response requires end_turn")
        if self.status != "ok" and not self.error:
            raise ValueError("Unsuccessful deliveries require a reason")
        return self


class Trial(Observation):
    index: int = Field(ge=1)
    passed: bool
    guardrail: Literal[
        "served",
        "withheld_schema",
        "withheld_unknown_citation",
        "withheld_unsupported_wording",
        "withheld_delivery",
    ]
    assertions: list[Assertion]


class CaseResult(Case):
    request_hashes: dict[Arm, str]
    baseline: list[Trial]
    candidate: list[Trial]


class Summary(Record):
    verdict: Verdict
    reasons: list[str]
    case_count: int
    cluster_count: int
    baseline_accuracy: float | None
    candidate_accuracy: float | None
    delta: float | None
    ci95: list[float] | None
    baseline_p95_ms: float | None
    candidate_p95_ms: float | None
    baseline_cost_usd: str
    candidate_cost_usd: str
    arms: dict[str, Any]
    losses: list[str]
    wins: list[str]


class Comparison(Record):
    version: Literal[1] = 1
    id: str
    title: str
    mode: Literal["fixture", "live"]
    provenance: dict[str, Any]
    policy: Policy
    trials_per_case: int = Field(ge=1, le=20)
    cases: list[CaseResult] = Field(min_length=1)
    summary: Summary

    @model_validator(mode="after")
    def paired(self) -> Self:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate case IDs in artifact")
        expected = list(range(1, self.trials_per_case + 1))
        for case in self.cases:
            if set(case.request_hashes) != set(ARMS):
                raise ValueError(f"{case.id} requires both request hashes")
            for arm in ARMS:
                if [t.index for t in getattr(case, arm)] != expected:
                    raise ValueError(f"Unpaired or out-of-order trials for {case.id}/{arm}")
        return self


def digest(data: Any) -> str:
    canonical = json.dumps(
        data, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def artifact_id(data: dict[str, Any]) -> str:
    return f"{data['mode']}-{digest({key: val for key, val in data.items() if key != 'id'})[:24]}"
