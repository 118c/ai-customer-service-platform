from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from agents.agent_orchestrator import AgentType, OrchestratorResult
from api.main import _employee_response
from core.intent_recognizer import IntentCategory, UrgencyLevel
from evaluation.faults import FaultInjectingBusinessGateway, FaultRule
from evaluation.full_workflow_evaluator import FullWorkflowEvaluator
from evaluation.schemas import WorkflowEvaluationCase
from integrations.business_services import LocalReferenceGateway
from orchestration.service_graph import EmployeeServiceWorkflow
from tests.test_service_graph import FakeMemory, FakeOrchestrator, FakeTools


class InMemoryTraceStore:
    def __init__(self):
        self.items = {}

    async def save(self, request_id, trace):
        self.items[request_id] = trace

    async def get(self, request_id):
        return self.items.get(request_id)


class MultiTurnOrchestrator:
    async def recognize_intent(self, message, history=None):
        intent = IntentCategory.APPROVAL_REQUEST if "提交" in message else IntentCategory.REQUEST
        return SimpleNamespace(
            intent=intent,
            intent_group="workflow" if "提交" in message else "general",
            confidence=0.95,
            source_scores={"llm": 0.95},
            entities={},
            urgency=UrgencyLevel.LOW,
        )

    async def run(self, request):
        agent = (
            AgentType.WORKFLOW
            if request.intent is IntentCategory.APPROVAL_REQUEST
            else AgentType.GENERAL
        )
        return OrchestratorResult(
            request_id=request.request_id,
            response=(
                "已整理领料申请，请核对后提交。"
                if agent is AgentType.WORKFLOW
                else "请继续补充具体物料。"
            ),
            agent_type=agent,
            intent=request.intent,
            agent_types=[agent],
            primary_agent=agent,
            routing_reason="deterministic test route",
            routing_confidence=0.95,
        )


async def test_full_workflow_evaluator_covers_review_and_idempotency(tmp_path):
    gateway = FaultInjectingBusinessGateway(LocalReferenceGateway(str(tmp_path / "services.db")))
    traces = InMemoryTraceStore()
    workflow = EmployeeServiceWorkflow(
        orchestrator=FakeOrchestrator(), memory=FakeMemory(), tool_manager=FakeTools(),
        business_gateway=gateway, checkpointer=InMemorySaver(),
    )
    report = await FullWorkflowEvaluator(
        workflow, "test-v1", trace_store=traces,
        public_response_builder=_employee_response,
    ).run([
        WorkflowEvaluationCase(
            case_id="approval-confirm", message="帮我提交领料申请",
            expected_intent="approval_request", expected_action_type="workflow.submit",
            decision="confirm", expect_receipt=True, verify_idempotency=True,
        )
    ])
    assert report.pass_rate == 1.0
    assert report.results[0].checks["confirmation_before_write"] is True
    assert report.results[0].checks["idempotent_confirmation"] is True
    assert report.results[0].checks["trace_linked"] is True
    assert report.results[0].checks["public_contract"] is True
    assert report.metrics["business_action_success_rate"] == 1.0
    assert report.metrics["confirmation_success_rate"] == 1.0
    assert report.metrics["workflow_resume_success_rate"] == 1.0
    assert report.metrics["duplicate_write_count"] == 0.0
    assert report.metrics["business_action_latency_ms"] >= 0.0
    assert gateway.execute_calls == 1


async def test_business_failure_is_reported_as_controlled_degradation(tmp_path):
    gateway = FaultInjectingBusinessGateway(
        LocalReferenceGateway(str(tmp_path / "services.db")),
        [FaultRule("business.execute")],
    )
    workflow = EmployeeServiceWorkflow(
        orchestrator=FakeOrchestrator(), memory=FakeMemory(), tool_manager=FakeTools(),
        business_gateway=gateway, checkpointer=InMemorySaver(),
    )
    initial = await workflow.invoke({
        "request_id": "failure-1", "user_id": "E1001", "conversation_id": "conv-failure",
        "message": "帮我提交领料申请", "started_at": 1.0,
    })
    completed = await workflow.resume("failure-1", {"decision": "approve"})
    assert initial["status"] == "awaiting_review"
    assert completed["status"] == "completed"
    assert completed["action_result"]["success"] is False
    assert completed["degradation_events"][-1]["component"] == "business.execute"

    gateway.controller.rules.append(FaultRule("business.execute"))
    report = await FullWorkflowEvaluator(
        workflow, "failure-v1", trace_store=InMemoryTraceStore(),
        public_response_builder=_employee_response,
    ).run([
        WorkflowEvaluationCase(
            case_id="business-write-failure", message="帮我提交领料申请",
            expected_intent="approval_request", expected_action_type="workflow.submit",
            decision="confirm", expected_degradation_components=["business.execute"],
        )
    ])
    assert report.pass_rate == 1.0
    assert report.metrics["controlled_degradation"] == 1.0
    assert report.metrics["business_action_success_rate"] == 0.0
    assert report.metrics["confirmation_success_rate"] == 1.0


async def test_multiturn_evaluation_links_memory_trace_and_public_contract(tmp_path):
    memory = FakeMemory()
    traces = InMemoryTraceStore()
    gateway = FaultInjectingBusinessGateway(LocalReferenceGateway(str(tmp_path / "services.db")))
    workflow = EmployeeServiceWorkflow(
        orchestrator=MultiTurnOrchestrator(), memory=memory, tool_manager=FakeTools(),
        business_gateway=gateway, checkpointer=InMemorySaver(),
    )
    report = await FullWorkflowEvaluator(
        workflow, "multiturn-v1", trace_store=traces,
        public_response_builder=_employee_response, model_provider="local-continuity",
    ).run([
        WorkflowEvaluationCase(
            case_id="material-request-edit",
            turns=["我需要申请领料", "帮我提交领料申请，物料编码 MAT-1008，数量 20"],
            expected_intent="approval_request", expected_primary_agent="workflow",
            expected_action="workflow.submit", confirmation="edit",
            confirmation_changes={"material_code": "MAT-1008", "quantity": 20},
            expected_receipt=True, verify_idempotency=True,
        )
    ])
    result = report.results[0]
    assert report.pass_rate == 1.0, result
    assert result.checks["memory_persisted"] is True
    assert result.checks["trace_linked"] is True
    assert result.checks["public_contract"] is True
    assert len(result.request_ids) == 2
    assert result.trace_ids == result.request_ids
