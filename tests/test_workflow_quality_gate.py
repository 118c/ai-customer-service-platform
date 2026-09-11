import json
from pathlib import Path
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from agents.agent_orchestrator import AgentOrchestrator
from api.main import _employee_response
from core.llm_gateway import LocalContinuityProvider, ResilientLLMGateway
from evaluation.faults import FaultInjectingBusinessGateway
from evaluation.full_workflow_evaluator import FullWorkflowEvaluator
from evaluation.schemas import WorkflowEvaluationCase
from integrations.business_services import LocalReferenceGateway
from orchestration.service_graph import EmployeeServiceWorkflow
from tests.test_full_workflow_evaluator import InMemoryTraceStore
from tests.test_service_graph import FakeMemory


class FixedKnowledgeTools:
    async def search_with_rewrite(self, _tool_name, query, top_k=3):
        if "夜班" in query or "考勤" in query:
            data = [{
                "document_id": "attendance-v1", "title": "三班制考勤与交接规范",
                "content": "跨日夜班以班次开始日期归属考勤日。", "score": 0.99,
            }]
        elif "工艺" in query or "规范" in query:
            data = [{
                "document_id": "process-v1", "title": "工艺文件使用规范",
                "content": "现场必须使用生效状态的最新版本工艺文件。", "score": 0.99,
            }]
        else:
            data = []
        return SimpleNamespace(success=True, data=data[:top_k])


async def test_deterministic_full_workflow_pass_rate_meets_release_gate(tmp_path):
    gateway = ResilientLLMGateway([LocalContinuityProvider()])
    orchestrator = AgentOrchestrator(api_key="offline", llm_gateway=gateway)
    traces = InMemoryTraceStore()
    workflow = EmployeeServiceWorkflow(
        orchestrator=orchestrator,
        memory=FakeMemory(),
        tool_manager=FixedKnowledgeTools(),
        business_gateway=FaultInjectingBusinessGateway(
            LocalReferenceGateway(str(tmp_path / "services.db"))
        ),
        checkpointer=InMemorySaver(),
    )
    cases = [
        WorkflowEvaluationCase(**json.loads(line))
        for line in Path("evaluation/datasets/workflow_cases.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    report = await FullWorkflowEvaluator(
        workflow,
        dataset_version="2026.09",
        trace_store=traces,
        public_response_builder=_employee_response,
        model_provider="local-continuity",
    ).run(cases)
    assert report.pass_rate >= 0.95, {
        result.case_id: result.detail for result in report.results if not result.passed
    }
    assert all(result.request_ids == result.trace_ids for result in report.results)
    assert report.metrics["cancelled_write_count"] == 0.0
    assert report.metrics["duplicate_write_count"] == 0.0
