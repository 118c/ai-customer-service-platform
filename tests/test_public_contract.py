import json

import pytest

from api.main import _employee_response
from core.intent_recognizer import IntentCategory, IntentRecognizer
from mcp.tool_manager import MCPToolManager
from monitor.trace_store import build_trace


class QueryBiasedGateway:
    async def complete(self, **_kwargs):
        return json.dumps({"intent": "workflow_query", "confidence": 0.96, "reasoning": "history"})


def test_employee_response_hides_orchestration_details():
    internal = {
        "request_id": "req-1",
        "conversation_id": "conv-1",
        "response": "我已经整理好申请信息。",
        "status": "awaiting_review",
        "intent": "repair_request",
        "intent_confidence": 0.94,
        "intent_source_scores": {"llm": 0.9},
        "primary_agent": "technical",
        "routing_reason": "domain match",
        "model_provider": "provider-a",
        "sources": [{"title": "设备报修规范", "excerpt": "发生故障后应先停机。", "score": 0.88}],
        "interrupts": [{
            "action_type": "repair.create",
            "payload": {"equipment_id": "EQ-A17", "area": "A3", "symptom": "E104"},
        }],
    }
    public = _employee_response(internal, "conv-1").model_dump()
    assert set(public) == {
        "request_id", "conversation_id", "answer", "status", "sources", "confirmation", "receipt"
    }
    assert public["status"] == "confirmation_required"
    assert public["confirmation"]["title"] == "确认提交设备报修"
    assert public["sources"][0]["title"] == "设备报修规范"
    assert "intent" not in public
    assert "primary_agent" not in public


def test_internal_trace_keeps_diagnostics_outside_employee_contract():
    trace = build_trace({
        "request_id": "req-1",
        "intent": "attendance_query",
        "intent_confidence": 0.91,
        "primary_agent": "policy",
        "knowledge_used": True,
    }, "provider-a")
    assert trace["intent_consensus"]["intent"] == "attendance_query"
    assert trace["routing"]["primary_agent"] == "policy"
    assert trace["model_provider"] == "provider-a"


@pytest.mark.asyncio
async def test_explicit_action_wins_over_query_biased_history():
    recognizer = IntentRecognizer(api_key="unused", llm_gateway=QueryBiasedGateway())
    result = await recognizer.recognize(
        "帮我提交领料申请，物料编码 MAT-1008，数量 20",
        history=[{"role": "user", "content": "跨日夜班的考勤日期怎么计算？"}],
    )
    assert result.intent is IntentCategory.APPROVAL_REQUEST
    assert result.source_scores["explicit_action"] == 1.0


def test_local_rerank_prefers_evidenced_policy_document():
    query = "跨日夜班的考勤日期怎么计算"
    attendance = {"title": "三班制考勤规范", "content": "跨日夜班以班次开始日期归属考勤日。"}
    equipment = {"title": "设备报修流程", "content": "设备故障应记录报警码。"}
    assert MCPToolManager._lexical_relevance(query, attendance) > 0
    assert MCPToolManager._lexical_relevance(query, equipment) == 0
