import time
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from agents.agent_orchestrator import AgentType, OrchestratorResult
from core.intent_recognizer import IntentCategory, UrgencyLevel
from integrations.business_services import LocalReferenceGateway
from orchestration.service_graph import EmployeeServiceWorkflow


class FakeMemoryContext:
    recent_messages = []

    def to_prompt_text(self):
        return ""


class FakeMemory:
    def __init__(self):
        self.messages = []

    async def get_context(self, user_id, conversation_id, query=""):
        return FakeMemoryContext()

    async def add_message(self, user_id, conversation_id, role, content):
        self.messages.append((role.value, content))


class FakeOrchestrator:
    async def recognize_intent(self, message, history=None):
        return SimpleNamespace(
            intent=IntentCategory.APPROVAL_REQUEST,
            intent_group="workflow",
            confidence=0.91,
            source_scores={"llm": 0.9, "embedding": 0.8, "pattern": 0.75},
            entities={},
            urgency=UrgencyLevel.LOW,
        )

    async def run(self, request):
        return OrchestratorResult(
            request_id=request.request_id,
            response="已生成领料申请草稿，请确认后提交。",
            agent_type=AgentType.WORKFLOW,
            intent=IntentCategory.APPROVAL_REQUEST,
            agent_types=[AgentType.WORKFLOW],
            primary_agent=AgentType.WORKFLOW,
            routing_reason="workflow intent",
            routing_confidence=0.91,
        )


class FakeTools:
    async def search_with_rewrite(self, *args, **kwargs):
        return SimpleNamespace(success=False, data=[])


async def test_graph_pauses_and_resumes_reviewed_action(tmp_path):
    memory = FakeMemory()
    workflow = EmployeeServiceWorkflow(
        orchestrator=FakeOrchestrator(),
        memory=memory,
        tool_manager=FakeTools(),
        business_gateway=LocalReferenceGateway(str(tmp_path / "services.db")),
        checkpointer=InMemorySaver(),
    )
    initial = await workflow.invoke({
        "request_id": "req-1",
        "user_id": "E1001",
        "conversation_id": "conv-1",
        "message": "帮我提交领料申请",
        "started_at": time.time(),
    })
    assert initial["status"] == "awaiting_review"
    assert initial["interrupts"][0]["action_type"] == "workflow.submit"
    assert memory.messages == []

    completed = await workflow.resume("req-1", {"decision": "approve"})
    assert completed["status"] == "completed"
    assert completed["action_result"]["success"] is True
    assert completed["action_result"]["record_id"].startswith("WF-")
    assert len(memory.messages) == 2

    repeated = await workflow.resume("req-1", {"decision": "approve"})
    assert repeated["action_result"]["record_id"] == completed["action_result"]["record_id"]
    assert len(memory.messages) == 2


async def test_graph_cancellation_does_not_create_business_record(tmp_path):
    gateway = LocalReferenceGateway(str(tmp_path / "services.db"))
    before = len(await gateway.list_tasks())
    workflow = EmployeeServiceWorkflow(
        orchestrator=FakeOrchestrator(), memory=FakeMemory(), tool_manager=FakeTools(),
        business_gateway=gateway, checkpointer=InMemorySaver(),
    )
    initial = await workflow.invoke({
        "request_id": "req-cancel", "user_id": "E1001", "conversation_id": "conv-cancel",
        "message": "帮我提交领料申请", "started_at": time.time(),
    })
    completed = await workflow.resume("req-cancel", {"decision": "reject"})
    assert initial["status"] == "awaiting_review"
    assert completed["status"] == "completed"
    assert not completed.get("action_result")
    assert len(await gateway.list_tasks()) == before
