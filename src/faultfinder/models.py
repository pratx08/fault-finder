"""Versioned boundaries between planning, execution, and verification."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verdict(StrEnum):
    NO_EVIDENCE = "no_evidence"
    SUSPECTED = "suspected"
    RETENTION = "reproduced_retention"
    VERIFIED = "verified_retention_bug"
    INCONCLUSIVE = "inconclusive"


class Policy(Model):
    sizes: list[int] = Field(default_factory=lambda: [0, 100, 500, 1000])
    repeats: int = Field(default=3, ge=3, le=10)
    warmup: int = Field(default=20, ge=1, le=1000)
    timeout_seconds: int = Field(default=900, ge=1, le=3600)
    memory_mb: int = Field(default=2048, ge=128, le=16384)
    noise_bytes: int = Field(default=65536, ge=0)

    @model_validator(mode="after")
    def validate_sizes(self) -> "Policy":
        if (
            len(self.sizes) < 3
            or self.sizes != sorted(set(self.sizes))
            or self.sizes[0] != 0
            or self.sizes[-1] > 100_000
        ):
            raise ValueError("sizes must start at zero and contain 3+ distinct increasing values")
        return self


class Candidate(Model):
    file: str
    line: int
    kind: str
    reason: str


class Plan(Model):
    kind: Literal["pytest", "probe", "demo"]
    title: str
    args: list[str] = Field(default_factory=list)
    harness: str = ""
    target_file: str = ""
    target_line: int = Field(default=0, ge=0)
    demo_case: str = ""
    policy: Policy = Field(default_factory=Policy)
    hypothesis: str = ""
    model: str = ""
    prompt_version: str = "1"


class Trial(Model):
    variant: Literal["subject", "control"]
    size: int = Field(ge=0, le=100000)
    repeat: int = Field(ge=0, le=9)
    ok: bool = False
    completed: int = Field(default=0, ge=0)
    natural_survivors: int | None = Field(default=None, ge=0)
    survivors: int | None = Field(default=None, ge=0)
    after_release: int | None = Field(default=None, ge=0)
    retained_bytes: int | None = None
    rss_bytes: int | None = Field(default=None, ge=0)
    path_hits: int = Field(default=0, ge=0)
    owner_types: list[str] = Field(default_factory=list)
    allocations: list[dict[str, str | int]] = Field(default_factory=list)
    error: str = ""
    runtime: dict[str, str] = Field(default_factory=dict)


class Assessment(Model):
    verdict: Verdict
    reason: str
    metric: str = ""
    growth_per_operation: float | None = None
    limitations: list[str] = Field(default_factory=list)


class Evidence(Model):
    schema_version: int = 1
    verifier_version: str = "1"
    id: str
    created_at: str
    project: dict
    plan: Plan
    trials: list[Trial]
    assessment: Assessment
    backend: str
    agent_review: str = ""
    limitations: list[str] = Field(default_factory=list)
