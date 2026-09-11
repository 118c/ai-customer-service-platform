"""LangGraph workflow for the enterprise employee service request lifecycle."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.agent_orchestrator import Request
from integrations.business_services import (
    BusinessAction,
    BusinessServiceGateway,
    plan_business_action,
    plan_business_query,
)
from memory.conversation_memory import MsgRole


class WorkflowState(TypedDict, total=False):
    request_id: str
    user_id: str
    conversation_id: str
    message: str
    started_at: float
    history: List[Dict[str, str]]
    context: str
    intent: str
    intent_group: str
    intent_confidence: float
    intent_source_scores: Dict[str, float]
    entities: Dict[str, List[str]]
    urgency: str
    knowledge_used: bool
    sources: List[Dict[str, Any]]
    business_context: Dict[str, Any]
    degradation_events: List[Dict[str, str]]
    knowledge_top_k: int
    response: str
    agent_type: str
    agent_types: List[str]
    primary_agent: str
    supporting_agents: List[str]
    routing_reason: str
    routing_confidence: float
    escalated: bool
    pending_action: Dict[str, Any]
    review_decision: Dict[str, Any]
    action_result: Dict[str, Any]
    status: str
    latency_ms: float


class EmployeeServiceWorkflow:
    def __init__(self, *, orchestrator: Any, memory: Any, tool_manager: Any,
                 business_gateway: BusinessServiceGateway, checkpointer: Any):
        self.orchestrator = orchestrator
        self.memory = memory
        self.tool_manager = tool_manager
        self.business_gateway = business_gateway
        self.graph = self._build().compile(checkpointer=checkpointer)

    def _build(self) -> StateGraph:
        builder = StateGraph(WorkflowState)
        builder.add_node("load_context", self._load_context)
        builder.add_node("recognize_intent", self._recognize_intent)
        builder.add_node("enrich_context", self._enrich_context)
        builder.add_node("execute_agents", self._execute_agents)
        builder.add_node("prepare_action", self._prepare_action)
        builder.add_node("human_review", self._human_review)
        builder.add_node("execute_action", self._execute_action)
        builder.add_node("persist_result", self._persist_result)

        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "recognize_intent")
        builder.add_edge("recognize_intent", "enrich_context")
        builder.add_edge("enrich_context", "execute_agents")
        builder.add_edge("execute_agents", "prepare_action")
        builder.add_conditional_edges(
            "prepare_action",
            self._after_prepare,
            {"review": "human_review", "execute": "execute_action", "persist": "persist_result"},
        )
        builder.add_conditional_edges(
            "human_review",
            self._after_review,
            {"execute": "execute_action", "persist": "persist_result"},
        )
        builder.add_edge("execute_action", "persist_result")
        builder.add_edge("persist_result", END)
        return builder

    async def _load_context(self, state: WorkflowState) -> Dict[str, Any]:
        try:
            memory = await self.memory.get_context(
                state["user_id"], state["conversation_id"], query=state["message"]
            )
        except Exception as ex:
            return {
                "history": [],
                "context": "",
                "status": "running",
                "degradation_events": [self._degradation("memory.read", ex)],
            }
        history = [
            {"role": item.role.value, "content": item.content}
            for item in memory.recent_messages[-5:]
        ]
        return {"history": history, "context": memory.to_prompt_text(), "status": "running"}

    async def _recognize_intent(self, state: WorkflowState) -> Dict[str, Any]:
        try:
            result = await self.orchestrator.recognize_intent(
                state["message"], history=state.get("history")
            )
        except Exception as ex:
            events = list(state.get("degradation_events", []))
            events.append(self._degradation("intent.recognize", ex))
            return {
                "intent": "other",
                "intent_group": "general",
                "intent_confidence": 0.0,
                "intent_source_scores": {},
                "entities": {},
                "urgency": "low",
                "degradation_events": events,
            }
        return {
            "intent": result.intent.value,
            "intent_group": result.intent_group,
            "intent_confidence": result.confidence,
            "intent_source_scores": result.source_scores,
            "entities": result.entities,
            "urgency": result.urgency.name.lower(),
        }

    async def _enrich_context(self, state: WorkflowState) -> Dict[str, Any]:
        parts = [state.get("context", "")]
        knowledge_used = False
        public_sources: List[Dict[str, Any]] = []
        degradation_events = list(state.get("degradation_events", []))
        if self._needs_knowledge(state.get("intent", "")):
            try:
                top_k = max(1, min(int(state.get("knowledge_top_k", 3)), 20))
                search = await self.tool_manager.search_with_rewrite(
                    "knowledge_search", state["message"], top_k=top_k
                )
                if search.success and isinstance(search.data, list) and search.data:
                    citations = []
                    for item in search.data[:top_k]:
                        if isinstance(item, dict) and item.get("content") and not item.get("fallback"):
                            title = str(item.get("title", "企业知识"))
                            excerpt = self._public_excerpt(str(item["content"]))
                            public_sources.append({
                                "document_id": str(item.get("document_id", "")),
                                "title": title,
                                "excerpt": excerpt,
                                "score": round(float(item.get("score", 0.0) or 0.0), 4),
                            })
                            citations.append(
                                f"- {title} (score={item.get('score', 0)}): "
                                f"{str(item['content'])[:600]}"
                            )
                    if citations:
                        parts.append("[企业知识库]\n" + "\n".join(citations))
                        knowledge_used = True
                elif not search.success:
                    degradation_events.append({
                        "component": "knowledge.search",
                        "error_type": "unavailable",
                        "message": str(getattr(search, "error", "检索未成功"))[:160],
                    })
            except Exception as ex:
                degradation_events.append(self._degradation("knowledge.search", ex))

        business_context: Dict[str, Any] = {}
        query = plan_business_query(state.get("intent", ""), state.get("entities"))
        if query:
            action_type, payload = query
            try:
                business_context = await self.business_gateway.query(action_type, payload)
                parts.append("[业务系统查询结果]\n" + json.dumps(business_context, ensure_ascii=False))
            except Exception as ex:
                degradation_events.append(self._degradation("business.query", ex))
        return {
            "context": "\n\n".join(part for part in parts if part),
            "knowledge_used": knowledge_used,
            "sources": public_sources,
            "business_context": business_context,
            "degradation_events": degradation_events,
        }

    async def _execute_agents(self, state: WorkflowState) -> Dict[str, Any]:
        from core.intent_recognizer import IntentCategory, UrgencyLevel

        request = Request(
            message=state["message"],
            user_id=state["user_id"],
            conv_id=state["conversation_id"],
            request_id=state["request_id"],
            context=state.get("context", ""),
            history=state.get("history"),
            entities=state.get("entities", {}),
            intent=IntentCategory(state["intent"]),
            intent_group=state.get("intent_group"),
            urgency=UrgencyLevel[state.get("urgency", "low").upper()],
            intent_confidence=state.get("intent_confidence", 0.0),
        )
        try:
            result = await self.orchestrator.run(request)
        except Exception as ex:
            events = list(state.get("degradation_events", []))
            events.append(self._degradation("agent.execute", ex))
            return {
                "response": "服务暂时繁忙，已保留本次请求，请稍后重试或联系人工服务。",
                "agent_type": "general",
                "agent_types": ["general"],
                "primary_agent": "general",
                "supporting_agents": [],
                "routing_reason": "controlled_degradation",
                "routing_confidence": 0.0,
                "escalated": False,
                "degradation_events": events,
            }
        return {
            "response": result.response,
            "agent_type": result.agent_type.value,
            "agent_types": [item.value for item in result.agent_types],
            "primary_agent": (result.primary_agent or result.agent_type).value,
            "supporting_agents": [item.value for item in result.supporting_agents],
            "routing_reason": result.routing_reason,
            "routing_confidence": result.routing_confidence,
            "escalated": result.escalated,
        }

    async def _prepare_action(self, state: WorkflowState) -> Dict[str, Any]:
        action = plan_business_action(
            state.get("intent", ""), state["message"], state["user_id"],
            state["conversation_id"], state.get("entities"), request_id=state["request_id"],
        )
        if not action and state.get("escalated"):
            action = BusinessAction(
                "handoff.create",
                {"reason": state["message"][:200], "user_id": state["user_id"],
                 "conversation_id": state["conversation_id"]},
                False,
                "创建人工协同任务",
                state["request_id"],
            )
        return {"pending_action": self._action_dict(action) if action else {}}

    async def _human_review(self, state: WorkflowState) -> Dict[str, Any]:
        action = state["pending_action"]
        decision = interrupt({
            "type": "business_action_review",
            "request_id": state["request_id"],
            "description": action["description"],
            "action_type": action["action_type"],
            "payload": action["payload"],
            "allowed_decisions": ["approve", "edit", "reject"],
        })
        if not isinstance(decision, dict):
            decision = {"decision": "reject", "reason": "invalid_review_payload"}
        return {"review_decision": decision}

    async def _execute_action(self, state: WorkflowState) -> Dict[str, Any]:
        raw = dict(state["pending_action"])
        decision = state.get("review_decision", {})
        if decision.get("decision") == "edit" and isinstance(decision.get("payload"), dict):
            raw["payload"] = {**raw.get("payload", {}), **decision["payload"]}
        action = BusinessAction(
            action_type=raw["action_type"],
            payload=raw["payload"],
            requires_review=raw["requires_review"],
            description=raw["description"],
            idempotency_key=raw.get("idempotency_key", state["request_id"]),
        )
        try:
            result = await self.business_gateway.execute(action)
            return {"action_result": result}
        except Exception as ex:
            events = list(state.get("degradation_events", []))
            events.append(self._degradation("business.execute", ex))
            return {
                "action_result": {
                    "success": False,
                    "status": "temporarily_unavailable",
                    "reason": type(ex).__name__,
                },
                "degradation_events": events,
            }

    async def _persist_result(self, state: WorkflowState) -> Dict[str, Any]:
        response = state.get("response", "")
        action_result = state.get("action_result", {})
        decision = state.get("review_decision", {})
        if decision.get("decision") == "reject":
            response += "\n\n已按你的选择取消提交。"
        elif action_result.get("success"):
            response += f"\n\n业务操作已受理，编号：{action_result.get('record_id')}。"
        elif action_result:
            response += "\n\n业务系统暂时未能受理该操作，请稍后重试；本次未重复创建事项。"
        events = list(state.get("degradation_events", []))
        try:
            await self.memory.add_message(state["user_id"], state["conversation_id"], MsgRole.USER, state["message"])
            await self.memory.add_message(state["user_id"], state["conversation_id"], MsgRole.ASSISTANT, response)
        except Exception as ex:
            events.append(self._degradation("memory.write", ex))
        return {
            "response": response,
            "status": "completed",
            "latency_ms": max(0.0, (time.time() - state["started_at"]) * 1000),
            "degradation_events": events,
        }

    @staticmethod
    def _after_prepare(state: WorkflowState) -> str:
        action = state.get("pending_action") or {}
        if not action:
            return "persist"
        return "review" if action.get("requires_review") else "execute"

    @staticmethod
    def _after_review(state: WorkflowState) -> str:
        return "execute" if state.get("review_decision", {}).get("decision") in {"approve", "edit"} else "persist"

    @staticmethod
    def _action_dict(action: Optional[BusinessAction]) -> Dict[str, Any]:
        if action is None:
            return {}
        return {
            "action_type": action.action_type,
            "payload": action.payload,
            "requires_review": action.requires_review,
            "description": action.description,
            "idempotency_key": action.idempotency_key,
        }

    @staticmethod
    def _public_excerpt(content: str) -> str:
        """Remove implementation vocabulary from employee-facing citations."""
        replacements = {
            "Agent": "智能客服",
            "agent": "智能客服",
            "RAG": "知识检索",
            "routing": "服务分派",
        }
        clean = content
        for source, target in replacements.items():
            clean = clean.replace(source, target)
        return clean[:220]

    @staticmethod
    def _needs_knowledge(intent: str) -> bool:
        return intent not in {"greeting", "feedback", "human_handoff", "escalation", "other"}

    async def invoke(self, state: WorkflowState) -> Dict[str, Any]:
        # One checkpoint thread per request keeps independent turns isolated while
        # conversation memory remains keyed by conversation_id.
        config = {"configurable": {"thread_id": state["request_id"]}}
        result = await self.graph.ainvoke(state, config=config)
        return self._public_result(result)

    async def resume(self, request_id: str, decision: Dict[str, Any]) -> Dict[str, Any]:
        config = {"configurable": {"thread_id": request_id}}
        snapshot = await self.graph.aget_state(config)
        if snapshot.values and not snapshot.next and snapshot.values.get("status") == "completed":
            return self._public_result(dict(snapshot.values))
        result = await self.graph.ainvoke(Command(resume=decision), config=config)
        return self._public_result(result)

    @staticmethod
    def _degradation(component: str, error: Exception) -> Dict[str, str]:
        return {
            "component": component,
            "error_type": type(error).__name__,
            "message": str(error)[:160],
        }

    @staticmethod
    def _public_result(result: Dict[str, Any]) -> Dict[str, Any]:
        interrupts = result.get("__interrupt__", [])
        if interrupts:
            values = [getattr(item, "value", item) for item in interrupts]
            return {
                **{key: value for key, value in result.items() if key != "__interrupt__"},
                "status": "awaiting_review",
                "interrupts": values,
            }
        return result
