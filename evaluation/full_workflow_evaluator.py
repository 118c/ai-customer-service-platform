"""Evaluation runner that exercises the complete LangGraph employee-service workflow."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from evaluation.rag_metrics import grounded_fact_score, retrieval_metrics


@dataclass
class WorkflowEvaluationCase:
    case_id: str
    message: str
    expected_intent: Optional[str] = None
    expected_action_type: Optional[str] = None
    expected_final_status: str = "completed"
    decision: Optional[str] = None
    relevant_documents: list[str] = field(default_factory=list)
    expected_facts: list[str] = field(default_factory=list)
    expect_receipt: bool = False
    verify_idempotency: bool = False
    expected_degradation_components: list[str] = field(default_factory=list)
    user_id: str = "E1001"


@dataclass
class WorkflowCaseResult:
    case_id: str
    passed: bool
    latency_ms: float
    checks: dict[str, bool]
    metrics: dict[str, float]
    detail: str = ""


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


class FullWorkflowEvaluator:
    def __init__(self, workflow: Any, dataset_version: str = "1"):
        self.workflow = workflow
        self.dataset_version = dataset_version

    async def run(self, cases: list[WorkflowEvaluationCase]) -> WorkflowEvaluationRun:
        started = self._now()
        run_id = uuid.uuid4().hex
        results: list[WorkflowCaseResult] = []
        for index, case in enumerate(cases):
            results.append(await self._run_case(case, run_id, index))

        metric_values: dict[str, list[float]] = {}
        for result in results:
            for name, value in result.metrics.items():
                metric_values.setdefault(name, []).append(value)
        metrics = {
            name: round(sum(values) / len(values), 4)
            for name, values in metric_values.items() if values
        }
        passed = sum(1 for result in results if result.passed)
        latencies = sorted(result.latency_ms for result in results)
        if latencies:
            metrics["latency_p50_ms"] = round(self._percentile(latencies, 0.50), 2)
            metrics["latency_p95_ms"] = round(self._percentile(latencies, 0.95), 2)
        metrics["workflow_success_rate"] = round(passed / len(results), 4) if results else 1.0
        metrics["failure_rate"] = round(1.0 - metrics["workflow_success_rate"], 4)
        self._add_check_rate(metrics, results, "business_success", "business_success_rate")
        review_checks = [
            value
            for result in results
            for name, value in result.checks.items()
            if name in {"confirmation_before_write", "cancel_prevents_write", "idempotent_confirmation"}
        ]
        if review_checks:
            metrics["review_flow_success_rate"] = round(
                sum(1 for value in review_checks if value) / len(review_checks), 4
            )
        return WorkflowEvaluationRun(
            run_id=run_id,
            suite="full-workflow",
            dataset_version=self.dataset_version,
            started_at=started,
            completed_at=self._now(),
            status="completed",
            pass_rate=round(passed / len(results), 4) if results else 1.0,
            metrics=metrics,
            metadata={"case_count": len(results), "passed": passed},
            results=results,
        )

    async def _run_case(
        self, case: WorkflowEvaluationCase, run_id: str, index: int
    ) -> WorkflowCaseResult:
        started = time.monotonic()
        request_id = f"eval-{run_id[:10]}-{index}"
        conversation_id = f"eval-conv-{run_id[:10]}-{index}"
        checks: dict[str, bool] = {}
        metrics: dict[str, float] = {}
        try:
            execute_before = self._execute_count()
            initial = await self.workflow.invoke({
                "request_id": request_id,
                "user_id": case.user_id,
                "conversation_id": conversation_id,
                "message": case.message,
                "started_at": time.time(),
            })
            execute_after_initial = self._execute_count()

            if case.expected_intent:
                checks["intent"] = initial.get("intent") == case.expected_intent
            pending = initial.get("pending_action") or {}
            if case.expected_action_type:
                checks["action_type"] = pending.get("action_type") == case.expected_action_type
                if pending.get("requires_review"):
                    no_result = not initial.get("action_result")
                    no_call = (
                        execute_before is None or execute_after_initial is None
                        or execute_before == execute_after_initial
                    )
                    checks["confirmation_before_write"] = no_result and no_call

            ranked, evidence = self._ranked_sources(initial, case.relevant_documents)
            if case.relevant_documents:
                metrics.update(retrieval_metrics(ranked, case.relevant_documents, k=3))
            if case.expected_facts:
                metrics["answer_faithfulness"] = round(
                    grounded_fact_score(initial.get("response", ""), evidence, case.expected_facts), 4
                )

            final = initial
            if case.decision:
                decision = self._decision(case.decision)
                final = await self.workflow.resume(request_id, decision)
                if decision["decision"] == "reject":
                    checks["cancel_prevents_write"] = not final.get("action_result")

            checks["final_status"] = final.get("status") == case.expected_final_status
            if case.expect_receipt:
                checks["business_success"] = bool(
                    isinstance(final.get("action_result"), dict)
                    and final["action_result"].get("success")
                    and final["action_result"].get("record_id")
                )

            if case.verify_idempotency and case.decision in {"confirm", "approve"}:
                first_record = (final.get("action_result") or {}).get("record_id")
                execute_before_repeat = self._execute_count()
                repeated = await self.workflow.resume(request_id, self._decision(case.decision))
                execute_after_repeat = self._execute_count()
                second_record = (repeated.get("action_result") or {}).get("record_id")
                checks["idempotent_confirmation"] = (
                    bool(first_record) and first_record == second_record
                    and (
                        execute_before_repeat is None or execute_after_repeat is None
                        or execute_before_repeat == execute_after_repeat
                    )
                )

            degradation = final.get("degradation_events", [])
            if case.expected_degradation_components:
                actual_components = {
                    item.get("component") for item in degradation if isinstance(item, dict)
                }
                checks["controlled_degradation"] = set(
                    case.expected_degradation_components
                ).issubset(actual_components)
                metrics["controlled_degradation"] = (
                    1.0 if checks["controlled_degradation"] else 0.0
                )
            passed = all(checks.values()) if checks else True
            detail = "" if passed else ", ".join(name for name, ok in checks.items() if not ok)
        except Exception as ex:
            passed = False
            detail = f"{type(ex).__name__}: {str(ex)[:240]}"
            checks["workflow_completed"] = False

        return WorkflowCaseResult(
            case_id=case.case_id,
            passed=passed,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            checks=checks,
            metrics=metrics,
            detail=detail,
        )

    def _execute_count(self) -> Optional[int]:
        value = getattr(self.workflow.business_gateway, "execute_calls", None)
        return int(value) if isinstance(value, int) else None

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if len(values) == 1:
            return values[0]
        position = (len(values) - 1) * percentile
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        fraction = position - lower
        return values[lower] + (values[upper] - values[lower]) * fraction

    @staticmethod
    def _add_check_rate(
        metrics: dict[str, float], results: list[WorkflowCaseResult],
        check_name: str, metric_name: str,
    ) -> None:
        values = [result.checks[check_name] for result in results if check_name in result.checks]
        if values:
            metrics[metric_name] = round(sum(1 for value in values if value) / len(values), 4)

    @staticmethod
    def _decision(value: str) -> dict[str, str]:
        normalized = value.lower()
        return {
            "decision": "approve" if normalized in {"confirm", "approve"} else "reject",
            "reason": "evaluation_case",
        }

    @staticmethod
    def _ranked_sources(
        result: dict[str, Any], relevant_documents: list[str]
    ) -> tuple[list[str], list[str]]:
        ranked: list[str] = []
        evidence: list[str] = []
        relevant = set(relevant_documents)
        for source in result.get("sources", []):
            if not isinstance(source, dict):
                continue
            title = str(source.get("title", ""))
            document_id = str(source.get("document_id", ""))
            ranked.append(title if title in relevant else document_id or title)
            evidence.append(str(source.get("excerpt", "")))
        return ranked, evidence

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
