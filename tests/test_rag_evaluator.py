import json
from pathlib import Path

from evaluation.rag_evaluator import RAGEvaluator
from evaluation.schemas import RAGEvaluationCase
from tests.test_full_workflow_evaluator import InMemoryTraceStore


class GroundedWorkflow:
    FACTS = {
        "rag-attendance-001": (
            "policy-attendance-v1", "班次开始日期", "三班制考勤与交接规范"
        ),
        "rag-equipment-001": (
            "equipment-repair-v1", "确保人员安全", "设备故障分级与报修流程"
        ),
        "rag-process-version-001": (
            "process-governance-v1", "工艺工程师确认", "工艺文件使用规范"
        ),
    }

    async def invoke(self, state):
        question = state["message"]
        if "夜班" in question:
            key = "rag-attendance-001"
        elif "P1" in question:
            key = "rag-equipment-001"
        else:
            key = "rag-process-version-001"
        document_id, fact, title = self.FACTS[key]
        return {
            **state,
            "status": "completed",
            "response": f"根据现行资料，应先确认{fact}。",
            "sources": [{
                "document_id": document_id,
                "title": title,
                "excerpt": f"受控文件要求先确认{fact}。",
                "score": 0.99,
            }],
        }


class StableJudge:
    async def complete(self, **_kwargs):
        return '{"faithfulness": 0.96, "answer_relevance": 0.95}'


async def test_rag_suite_meets_deterministic_and_judge_thresholds():
    cases = [
        RAGEvaluationCase(**json.loads(line))
        for line in Path("evaluation/datasets/rag_cases.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    traces = InMemoryTraceStore()
    report = await RAGEvaluator(
        GroundedWorkflow(), llm_judge=StableJudge(), dataset_version="2026.09",
        trace_store=traces, model_provider="stable-judge",
    ).run(cases)
    assert report.pass_rate == 1.0, {
        result.case_id: result.detail for result in report.results if not result.passed
    }
    assert report.metrics["recall@3"] == 1.0
    assert report.metrics["citation_precision"] == 1.0
    assert report.metrics["faithfulness_rule"] == 1.0
    assert report.metrics["faithfulness_judge"] == 0.96
    assert report.metrics["faithfulness"] == 0.96
    assert all(result.request_ids == result.trace_ids for result in report.results)
