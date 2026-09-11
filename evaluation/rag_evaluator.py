"""RAG-specific evaluator combining deterministic evidence checks with an optional LLM judge."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from evaluation.rag_metrics import (
    answer_relevance_score,
    citation_precision,
    citation_recall,
    grounded_fact_score,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)
from evaluation.schemas import RAGCaseResult, RAGEvaluationCase, RAGEvaluationRun


RAG_THRESHOLDS = {
    "recall@3": 0.90,
    "mrr": 0.80,
    "ndcg@3": 0.85,
    "citation_precision": 0.90,
    "citation_recall": 0.85,
    "faithfulness": 0.85,
    "answer_relevance": 0.85,
}


class RAGEvaluator:
    def __init__(
        self,
        workflow: Any,
        llm_judge: Any = None,
        dataset_version: str = "1",
        *,
        trace_store: Any = None,
        model_provider: str | Any = "",
    ):
        self.workflow = workflow
        self.llm_judge = llm_judge
        self.dataset_version = dataset_version
        self.trace_store = trace_store
        self.model_provider = model_provider

    async def run(self, cases: list[RAGEvaluationCase]) -> RAGEvaluationRun:
        started_at = datetime.now(timezone.utc).isoformat()
        run_id = uuid.uuid4().hex
        results = [await self._run_case(case, run_id, index) for index, case in enumerate(cases)]
        names = {name for result in results for name in result.metrics}
        metrics = {
            name: round(
                sum(result.metrics[name] for result in results if name in result.metrics)
                / sum(1 for result in results if name in result.metrics),
                4,
            )
            for name in names
        }
        passed = sum(1 for result in results if result.passed)
        return RAGEvaluationRun(
            run_id=run_id,
            suite="rag",
            dataset_version=self.dataset_version,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            status="completed",
            pass_rate=round(passed / len(results), 4) if results else 1.0,
            metrics=metrics,
            metadata={"case_count": len(results), "passed": passed},
            results=results,
        )

    async def _run_case(self, case: RAGEvaluationCase, run_id: str, index: int) -> RAGCaseResult:
        started = time.monotonic()
        request_id = f"rag-eval-{run_id[:10]}-{index}"
        result = await self.workflow.invoke({
            "request_id": request_id,
            "user_id": case.user_id,
            "conversation_id": f"rag-conv-{run_id[:10]}-{index}",
            "message": case.question,
            "started_at": time.time(),
            "knowledge_top_k": max(1, min(case.top_k, 20)),
        })
        trace_linked = await self._save_trace(result)
        sources = [item for item in result.get("sources", []) if isinstance(item, dict)]
        retrieved = [str(item.get("document_id") or item.get("title", "")) for item in sources]
        cited = retrieved[:case.top_k]
        evidence = [str(item.get("excerpt", "")) for item in sources]
        answer = str(result.get("response", ""))
        relevant = case.relevant_document_ids
        expected_citations = case.expected_citations or relevant

        rule_faithfulness = grounded_fact_score(answer, evidence, case.standard_facts)
        rule_relevance = answer_relevance_score(answer, case.standard_facts)
        judge = await self._judge(case.question, answer, evidence)
        judge_faithfulness = judge.get("faithfulness")
        judge_relevance = judge.get("answer_relevance")
        faithfulness = (
            min(rule_faithfulness, judge_faithfulness)
            if judge_faithfulness is not None else rule_faithfulness
        )
        answer_relevance = (
            min(rule_relevance, judge_relevance)
            if judge_relevance is not None else rule_relevance
        )
        metrics = {
            f"recall@{case.top_k}": round(recall_at_k(retrieved, relevant, case.top_k), 4),
            "mrr": round(reciprocal_rank(retrieved, relevant), 4),
            f"ndcg@{case.top_k}": round(ndcg_at_k(retrieved, relevant, case.top_k), 4),
            "citation_precision": round(citation_precision(cited, expected_citations), 4),
            "citation_recall": round(citation_recall(cited, expected_citations), 4),
            "faithfulness_rule": round(rule_faithfulness, 4),
            "faithfulness": round(faithfulness, 4),
            "answer_relevance_rule": round(rule_relevance, 4),
            "answer_relevance": round(answer_relevance, 4),
        }
        if judge_faithfulness is not None:
            metrics["faithfulness_judge"] = round(judge_faithfulness, 4)
        if judge_relevance is not None:
            metrics["answer_relevance_judge"] = round(judge_relevance, 4)
        checks = {
            "forbidden_facts_absent": not any(fact in answer for fact in case.forbidden_facts),
            "no_fallback_citations": not any(item.get("fallback") for item in sources),
        }
        if self.trace_store is not None:
            checks["trace_linked"] = trace_linked
        for name, threshold in RAG_THRESHOLDS.items():
            metric_name = name.replace("@3", f"@{case.top_k}")
            checks[f"threshold:{name}"] = metrics.get(metric_name, 0.0) >= threshold
        passed = all(checks.values())
        return RAGCaseResult(
            case_id=case.case_id,
            passed=passed,
            metrics=metrics,
            checks=checks,
            detail="" if passed else ", ".join(name for name, ok in checks.items() if not ok),
            request_id=request_id,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            request_ids=[request_id],
            trace_ids=[request_id] if trace_linked else [],
        )

    async def _save_trace(self, result: dict[str, Any]) -> bool:
        if self.trace_store is None or not result.get("request_id"):
            return False
        from monitor.trace_store import build_trace

        provider = self.model_provider() if callable(self.model_provider) else self.model_provider
        await self.trace_store.save(
            result["request_id"], build_trace(result, str(provider or ""))
        )
        trace = await self.trace_store.get(result["request_id"])
        return bool(trace and trace.get("request_id") == result["request_id"])

    async def _judge(self, question: str, answer: str, evidence: list[str]) -> dict[str, float]:
        if self.llm_judge is None:
            return {}
        try:
            raw = await self.llm_judge.complete(
                system=(
                    "你是企业知识问答评测器。只返回 JSON，字段 faithfulness 和 "
                    "answer_relevance，取值为 0 到 1。证据不足时不得给高分。"
                ),
                messages=[{
                    "role": "user",
                    "content": json.dumps(
                        {"question": question, "answer": answer, "evidence": evidence},
                        ensure_ascii=False,
                    ),
                }],
                max_tokens=160,
                temperature=0.0,
            )
            payload = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            return {
                name: max(0.0, min(1.0, float(payload[name])))
                for name in ("faithfulness", "answer_relevance") if name in payload
            }
        except Exception:
            return {}
