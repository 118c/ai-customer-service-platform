from langgraph.checkpoint.memory import InMemorySaver

from evaluation.faults import FaultInjectingBusinessGateway, FaultRule
from evaluation.full_workflow_evaluator import FullWorkflowEvaluator, WorkflowEvaluationCase
from integrations.business_services import LocalReferenceGateway
from orchestration.service_graph import EmployeeServiceWorkflow
from tests.test_service_graph import FakeMemory, FakeOrchestrator, FakeTools


async def test_full_workflow_evaluator_covers_review_and_idempotency(tmp_path):
    gateway = FaultInjectingBusinessGateway(LocalReferenceGateway(str(tmp_path / "services.db")))
    workflow = EmployeeServiceWorkflow(
        orchestrator=FakeOrchestrator(), memory=FakeMemory(), tool_manager=FakeTools(),
        business_gateway=gateway, checkpointer=InMemorySaver(),
    )
    report = await FullWorkflowEvaluator(workflow, "test-v1").run([
        WorkflowEvaluationCase(
            case_id="approval-confirm", message="帮我提交领料申请",
            expected_intent="approval_request", expected_action_type="workflow.submit",
            decision="confirm", expect_receipt=True, verify_idempotency=True,
        )
    ])
    assert report.pass_rate == 1.0
    assert report.results[0].checks["confirmation_before_write"] is True
    assert report.results[0].checks["idempotent_confirmation"] is True
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

    # The rule has already fired once above, so install a new controlled failure for the evaluator run.
    gateway.controller.rules.append(FaultRule("business.execute"))
    report = await FullWorkflowEvaluator(workflow, "failure-v1").run([
        WorkflowEvaluationCase(
            case_id="business-write-failure", message="帮我提交领料申请",
            expected_intent="approval_request", expected_action_type="workflow.submit",
            decision="confirm", expected_degradation_components=["business.execute"],
        )
    ])
    assert report.pass_rate == 1.0
    assert report.metrics["controlled_degradation"] == 1.0
