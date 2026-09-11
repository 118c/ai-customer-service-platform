"""Versioned contracts shared by evaluation datasets, runners and repositories."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class WorkflowEvaluationCase:
    case_id: str
    message: str = ""
    turns: list[str] = field(default_factory=list)
    expected_intent: Optional[str] = None
    expected_primary_agent: Optional[str] = None
    expected_action: Optional[str] = None
    expected_action_type: Optional[str] = None
    expected_status: str = ""
    expected_final_status: str = "completed"
    confirmation: Optional[str] = None
    decision: Optional[str] = None
    confirmation_changes: dict[str, Any] = field(default_factory=dict)
    expected_sources: list[str] = field(default_factory=list)
    relevant_documents: list[str] = field(default_factory=list)
    standard_facts: list[str] = field(default_factory=list)
    expected_facts: list[str] = field(default_factory=list)
    forbidden_facts: list[str] = field(default_factory=list)
    top_k: int = 3
    expected_receipt: Optional[bool] = None
    expect_receipt: bool = False
    verify_idempotency: bool = False
    expect_memory_persisted: bool = True
    expect_trace: bool = True
    validate_public_contract: bool = True
    expected_degradation_components: list[str] = field(default_factory=list)
    user_id: str = "E1001"

    def input_turns(self) -> list[str]:
        return [item for item in (self.turns or [self.message]) if item.strip()]

    def action_name(self) -> Optional[str]:
        return self.expected_action or self.expected_action_type

    def confirmation_name(self) -> Optional[str]:
        return self.confirmation or self.decision

    def status_name(self) -> str:
        return self.expected_status or self.expected_final_status

    def source_targets(self) -> list[str]:
        return self.expected_sources or self.relevant_documents

    def fact_targets(self) -> list[str]:
        return self.standard_facts or self.expected_facts

    def receipt_required(self) -> bool:
        return self.expected_receipt if self.expected_receipt is not None else self.expect_receipt


@dataclass
class WorkflowCaseResult:
    case_id: str
    passed: bool
    latency_ms: float
    checks: dict[str, bool]
    metrics: dict[str, float]
    detail: str = ""
    request_ids: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowEvaluationRun:
    run_id: str
    suite: str
    dataset_version: str
    started_at: str
    completed_at: str
    status: str
    pass_rate: float
    metrics: dict[str, float]
    metadata: dict[str, Any]
    results: list[WorkflowCaseResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RAGEvaluationCase:
    case_id: str
    question: str
    relevant_document_ids: list[str]
    standard_facts: list[str]
    forbidden_facts: list[str] = field(default_factory=list)
    expected_citations: list[str] = field(default_factory=list)
    top_k: int = 3
    user_id: str = "E1001"


@dataclass
class RAGCaseResult:
    case_id: str
    passed: bool
    metrics: dict[str, float]
    checks: dict[str, bool]
    detail: str = ""
    request_id: str = ""
    latency_ms: float = 0.0
    request_ids: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGEvaluationRun:
    run_id: str
    suite: str
    dataset_version: str
    started_at: str
    completed_at: str
    status: str
    pass_rate: float
    metrics: dict[str, float]
    metadata: dict[str, Any]
    results: list[RAGCaseResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
