import time

from agents.agent_orchestrator import AgentOrchestrator, AgentType, Request
from core.intent_recognizer import IntentCategory, UrgencyLevel


class OfflineGateway:
    async def complete(self, **_kwargs):
        return "服务可用"


def test_monitor_isolates_agent_after_two_unhealthy_cycles_and_routes_around_it():
    orchestrator = AgentOrchestrator(api_key="offline", llm_gateway=OfflineGateway())
    technical = orchestrator._pool[AgentType.TECHNICAL][0]

    orchestrator.update_routing_penalties({"technical_0": 0.5})
    assert technical.stats.health_state == "degraded"
    assert orchestrator._best_agent(AgentType.TECHNICAL) is technical

    orchestrator.update_routing_penalties({"technical_0": 0.5})
    assert technical.stats.health_state == "isolated"
    assert orchestrator._best_agent(AgentType.TECHNICAL) is None

    decision = orchestrator._route_decision(Request(
        message="设备报警E104", user_id="E1001", conv_id="conv-1",
        intent=IntentCategory.EQUIPMENT_FAULT,
        urgency=UrgencyLevel.HIGH, intent_confidence=0.95,
    ))
    assert decision.primary_agent is AgentType.GENERAL

    technical.stats.isolated_until = time.monotonic() - 1
    assert orchestrator._best_agent(AgentType.TECHNICAL) is technical
    assert technical.stats.health_state == "half_open"
