"""Evaluation runner that exercises the complete LangGraph employee-service workflow."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from evaluation.rag_metrics import grounded_fact_score, retrieval_metrics
from evaluation.schemas import WorkflowCaseResult, WorkflowEvaluationCase, WorkflowEvaluationRun


class FullWorkflowEvaluator:
    COUNT_METRICS = {"cancelled_write_count", "duplicate_write_count", "idempotency_hit_count"}
    PUBLIC_FIELDS = {
        "request_id", "conversation_id", "answer", "status", "sources", "confirmation", "receipt"
    }
    INTERNAL_FIELDS = {
        "intent", "intent_confidence", "intent_source_scores", "agent_type", "agent_types",
        "primary_agent", "supporting_agents", "routing_reason", "routing_confidence",
        "model_provider", "degradation_events", "pending_action", "review_decision",
    }

    def __init__(
        self,
        workflow: Any,
        dataset_version: str = "1",
        *,
        trace_store: Any = None,
        public_response_builder: Optional[Callable[[dict[str, Any], str], Any]] = None,
        model_provider: str | Callable[[], str] = "",
    ):
        self.workflow = workflow
        self.dataset_version = dataset_version
        self.trace_store = trace_store
        self.public_response_builder = public_response_builder
        self.model_provider = model_provider

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
            name: round(
                sum(values) if name in self.COUNT_METRICS else sum(values) / len(values), 4
            )
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
        self._add_check_rate(
            metrics, results, "confirmation_success", "confirmation_success_rate"
        )
        self._add_check_rate(
            metrics, results, "workflow_resume_success", "workflow_resume_success_rate"
        )
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
        conversation_id = f"eval-conv-{run_id[:10]}-{index}"
        checks: dict[str, bool] = {}
        metrics: dict[str, float] = {}
        request_ids: list[str] = []
        trace_ids: list[str] = []
        try:
            turns = case.input_turns()
            if not turns:
                raise ValueError("evaluation case has no input turns")
            execute_before = self._execute_count()
            writes_before = self._gateway_counter("successful_writes")
            duplicate_before = self._gateway_counter("duplicate_write_count")
            idempotency_before = self._gateway_counter("idempotency_hit_count")
            latency_count_before = len(getattr(
                self.workflow.business_gateway, "execute_latencies_ms", []
            ))
            initial: dict[str, Any] = {}
            for turn_index, message in enumerate(turns):
                request_id = f"eval-{run_id[:10]}-{index}-{turn_index}"
                request_ids.append(request_id)
                initial = await self.workflow.invoke({
                    "request_id": request_id,
                    "user_id": case.user_id,
                    "conversation_id": conversation_id,
                    "message": message,
                    "started_at": time.time(),
                    "knowledge_top_k": max(1, min(case.top_k, 20)),
                })
                if turn_index < len(turns) - 1 and initial.get("status") == "awaiting_review":
                    raise ValueError("only the final turn may require confirmation")
                if await self._save_and_verify_trace(initial):
                    trace_ids.append(request_id)
            execute_after_initial = self._execute_count()

            if case.expected_intent:
                checks["intent"] = initial.get("intent") == case.expected_intent
            if case.expected_primary_agent:
                checks["primary_agent"] = initial.get("primary_agent") == case.expected_primary_agent
            pending = initial.get("pending_action") or {}
            expected_action = case.action_name()
            if expected_action:
                checks["action_type"] = pending.get("action_type") == expected_action
                if pending.get("requires_review"):
                    no_result = not initial.get("action_result")
                    no_call = (
                        execute_before is None or execute_after_initial is None
                        or execute_before == execute_after_initial
                    )
                    checks["confirmation_before_write"] = no_result and no_call

            source_targets = case.source_targets()
            ranked, evidence = self._ranked_sources(initial, source_targets)
            if source_targets:
                metrics.update(retrieval_metrics(ranked, source_targets, k=case.top_k))
                checks["expected_sources"] = set(source_targets).issubset(set(ranked[:case.top_k]))
            fact_targets = case.fact_targets()
            if fact_targets:
                metrics["answer_faithfulness"] = round(
                    grounded_fact_score(initial.get("response", ""), evidence, fact_targets), 4
                )
                checks["answer_faithfulness"] = metrics["answer_faithfulness"] == 1.0
            if case.forbidden_facts:
                answer = initial.get("response", "")
                checks["forbidden_facts_absent"] = not any(
                    item in answer for item in case.forbidden_facts
                )
            if case.validate_public_contract:
                checks["public_contract"] = bool(
                    self.public_response_builder
                    and self._valid_public_contract(initial, conversation_id)
                )

            final = initial
            confirmation = case.confirmation_name()
            request_id = request_ids[-1]
            if confirmation:
                decision = self._decision(confirmation, case.confirmation_changes)
                final = await self.workflow.resume(request_id, decision)
                if await self._save_and_verify_trace(final) and request_id not in trace_ids:
                    trace_ids.append(request_id)
                if decision["decision"] == "reject":
                    checks["cancel_prevents_write"] = not final.get("action_result")
                checks["workflow_resume_success"] = final.get("status") == "completed"
                checks["confirmation_success"] = (
                    final.get("status") == "completed"
                    and (
                        not final.get("action_result")
                        if decision["decision"] == "reject"
                        else bool(final.get("review_decision"))
                    )
                )

            checks["final_status"] = final.get("status") == case.status_name()
            has_receipt = bool(
                isinstance(final.get("action_result"), dict)
                and final["action_result"].get("success")
                and final["action_result"].get("record_id")
            )
            if case.expected_receipt is not None:
                checks["receipt_expectation"] = has_receipt is case.expected_receipt
                if case.expected_receipt:
                    checks["business_success"] = has_receipt
            elif case.expect_receipt:
                checks["business_success"] = has_receipt
            if expected_action and confirmation not in {"cancel", "reject"}:
                metrics["business_action_success_rate"] = 1.0 if has_receipt else 0.0
                expected_business_failure = (
                    "business.execute" in case.expected_degradation_components
                )
                if not expected_business_failure:
                    checks["business_action_success"] = has_receipt

            if case.verify_idempotency and confirmation in {"confirm", "approve", "edit"}:
                first_record = (final.get("action_result") or {}).get("record_id")
                execute_before_repeat = self._execute_count()
                repeated = await self.workflow.resume(
                    request_id, self._decision(confirmation, case.confirmation_changes)
                )
                execute_after_repeat = self._execute_count()
                second_record = (repeated.get("action_result") or {}).get("record_id")
                checks["idempotent_confirmation"] = (
                    bool(first_record) and first_record == second_record
                    and (
                        execute_before_repeat is None or execute_after_repeat is None
                        or execute_before_repeat == execute_after_repeat
                    )
                )

            writes_after = self._gateway_counter("successful_writes")
            duplicate_after = self._gateway_counter("duplicate_write_count")
            idempotency_after = self._gateway_counter("idempotency_hit_count")
            if confirmation in {"cancel", "reject"} and writes_before is not None and writes_after is not None:
                metrics["cancelled_write_count"] = float(max(0, writes_after - writes_before))
            if duplicate_before is not None and duplicate_after is not None:
                metrics["duplicate_write_count"] = float(max(0, duplicate_after - duplicate_before))
            if idempotency_before is not None and idempotency_after is not None:
                metrics["idempotency_hit_count"] = float(max(0, idempotency_after - idempotency_before))
            latencies = getattr(self.workflow.business_gateway, "execute_latencies_ms", [])
            if len(latencies) > latency_count_before:
                metrics["business_action_latency_ms"] = round(
                    float(latencies[-1]), 2
                )

            if case.expect_memory_persisted and final.get("status") == "completed":
                checks["memory_persisted"] = await self._memory_contains(
                    case.user_id, conversation_id, turns, final.get("response", "")
                )
            if case.expect_trace:
                checks["trace_linked"] = bool(
                    self.trace_store is not None
                    and set(request_ids).issubset(set(trace_ids))
                )
            if case.validate_public_contract:
                checks["public_contract"] = (
                    checks.get("public_contract", True)
                    and bool(
                        self.public_response_builder
                        and self._valid_public_contract(final, conversation_id)
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
            request_ids=request_ids,
            trace_ids=trace_ids,
            metadata={"turn_count": len(request_ids), "conversation_id": conversation_id},
        )

    def _execute_count(self) -> Optional[int]:
        value = getattr(self.workflow.business_gateway, "execute_calls", None)
        return int(value) if isinstance(value, int) else None

    def _gateway_counter(self, name: str) -> Optional[int]:
        value = getattr(self.workflow.business_gateway, name, None)
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
    def _decision(value: str, changes: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        normalized = value.lower()
        decision: dict[str, Any] = {
            "decision": (
                "edit" if normalized == "edit"
                else "approve" if normalized in {"confirm", "approve"}
                else "reject"
            ),
            "reason": "evaluation_case",
        }
        if decision["decision"] == "edit":
            decision["payload"] = changes or {}
        return decision

    async def _save_and_verify_trace(self, result: dict[str, Any]) -> bool:
        if self.trace_store is None or not result.get("request_id"):
            return False
        from monitor.trace_store import build_trace

        provider = self.model_provider() if callable(self.model_provider) else self.model_provider
        await self.trace_store.save(result["request_id"], build_trace(result, provider))
        trace = await self.trace_store.get(result["request_id"])
        return bool(trace and trace.get("request_id") == result["request_id"])

    async def _memory_contains(
        self, user_id: str, conversation_id: str, turns: list[str], response: str
    ) -> bool:
        memory = self.workflow.memory
        raw_messages = getattr(memory, "messages", None)
        if isinstance(raw_messages, list):
            contents = [str(item[-1]) for item in raw_messages if isinstance(item, (tuple, list)) and item]
            return all(turn in contents for turn in turns) and response in contents
        try:
            context = await memory.get_context(user_id, conversation_id, query=turns[-1])
            items = getattr(context, "recent_messages", [])
            contents = [str(getattr(item, "content", "")) for item in items]
            return turns[-1] in contents and response in contents
        except Exception:
            return False

    def _valid_public_contract(self, result: dict[str, Any], conversation_id: str) -> bool:
        try:
            public = self.public_response_builder(result, conversation_id)
            data = public.model_dump() if hasattr(public, "model_dump") else dict(public)
        except Exception:
            return False
        if set(data) != self.PUBLIC_FIELDS:
            return False
        return not self._contains_internal_key(data)

    def _contains_internal_key(self, value: Any) -> bool:
        if isinstance(value, dict):
            if self.INTERNAL_FIELDS.intersection(value):
                return True
            return any(self._contains_internal_key(item) for item in value.values())
        if isinstance(value, list):
            return any(self._contains_internal_key(item) for item in value)
        return False

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
