import time

from langgraph.checkpoint.memory import InMemorySaver

from evaluation.faults import (
    FaultInjectingMemory,
    FaultInjectingOrchestrator,
    FaultInjectingToolManager,
    FaultRule,
)
from integrations.business_services import LocalReferenceGateway
from orchestration.service_graph import EmployeeServiceWorkflow
from tests.test_service_graph import FakeMemory, FakeOrchestrator, FakeTools


def build_workflow(tmp_path, *, orchestrator=None, memory=None, tools=None):
    return EmployeeServiceWorkflow(
        orchestrator=orchestrator or FakeOrchestrator(),
        memory=memory or FakeMemory(),
        tool_manager=tools or FakeTools(),
        business_gateway=LocalReferenceGateway(str(tmp_path / "services.db")),
        checkpointer=InMemorySaver(),
    )


async def test_knowledge_timeout_keeps_confirmation_flow_available(tmp_path):
    tools = FaultInjectingToolManager(FakeTools(), [FaultRule("knowledge.search", "timeout")])
    workflow = build_workflow(tmp_path, tools=tools)
    result = await workflow.invoke({
        "request_id": "knowledge-failure", "user_id": "E1001", "conversation_id": "conv-1",
        "message": "帮我提交领料申请", "started_at": time.time(),
    })
    assert result["status"] == "awaiting_review"
    assert "knowledge.search" in {item["component"] for item in result["degradation_events"]}


async def test_memory_failures_do_not_break_request_completion(tmp_path):
    memory = FaultInjectingMemory(
        FakeMemory(), [FaultRule("memory.read"), FaultRule("memory.write")]
    )
    workflow = build_workflow(tmp_path, memory=memory)
    initial = await workflow.invoke({
        "request_id": "memory-failure", "user_id": "E1001", "conversation_id": "conv-2",
        "message": "帮我提交领料申请", "started_at": time.time(),
    })
    completed = await workflow.resume("memory-failure", {"decision": "reject"})
    components = {item["component"] for item in completed["degradation_events"]}
    assert completed["status"] == "completed"
    assert {"memory.read", "memory.write"}.issubset(components)


async def test_intent_and_agent_boundary_failures_return_controlled_response(tmp_path):
    orchestrator = FaultInjectingOrchestrator(
        FakeOrchestrator(), [FaultRule("intent.recognize"), FaultRule("agent.execute")]
    )
    workflow = build_workflow(tmp_path, orchestrator=orchestrator)
    result = await workflow.invoke({
        "request_id": "agent-failure", "user_id": "E1001", "conversation_id": "conv-3",
        "message": "需要帮助", "started_at": time.time(),
    })
    components = {item["component"] for item in result["degradation_events"]}
    assert result["status"] == "completed"
    assert {"intent.recognize", "agent.execute"}.issubset(components)
    assert "稍后重试" in result["response"]
