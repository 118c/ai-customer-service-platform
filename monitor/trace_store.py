"""Persistence boundary for internal request traces."""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Dict, Optional

import redis.asyncio as redis


class TraceStore:
    """Store trace summaries in Redis and degrade to a bounded process-local cache."""

    def __init__(self, redis_url: str, ttl_seconds: int = 86400, local_limit: int = 500):
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._redis_available = True
        self._ttl_seconds = ttl_seconds
        self._local_limit = local_limit
        self._local: OrderedDict[str, Dict[str, Any]] = OrderedDict()

    async def save(self, request_id: str, trace: Dict[str, Any]) -> None:
        if self._redis_available:
            try:
                await self._redis.setex(
                    f"trace:{request_id}",
                    self._ttl_seconds,
                    json.dumps(trace, ensure_ascii=False),
                )
                return
            except Exception:
                self._redis_available = False
        self._local[request_id] = trace
        self._local.move_to_end(request_id)
        while len(self._local) > self._local_limit:
            self._local.popitem(last=False)

    async def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        if self._redis_available:
            try:
                raw = await self._redis.get(f"trace:{request_id}")
                return json.loads(raw) if raw else None
            except Exception:
                self._redis_available = False
        return self._local.get(request_id)

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:
            pass


def build_trace(result: Dict[str, Any], model_provider: str = "") -> Dict[str, Any]:
    """Project workflow state into an internal, bounded observability document."""
    return {
        "request_id": result.get("request_id", ""),
        "conversation_id": result.get("conversation_id", ""),
        "status": result.get("status", "completed"),
        "input": result.get("message", ""),
        "intent_consensus": {
            "intent": result.get("intent", "other"),
            "group": result.get("intent_group", "other"),
            "confidence": result.get("intent_confidence", 0.0),
            "source_scores": result.get("intent_source_scores", {}),
            "entities": result.get("entities", {}),
            "urgency": result.get("urgency", "low"),
        },
        "routing": {
            "primary_agent": result.get("primary_agent", result.get("agent_type", "general")),
            "supporting_agents": result.get("supporting_agents", []),
            "reason": result.get("routing_reason", ""),
            "confidence": result.get("routing_confidence", 0.0),
            "escalated": bool(result.get("escalated", False)),
        },
        "retrieval": {
            "used": bool(result.get("knowledge_used", False)),
            "sources": result.get("sources", []),
        },
        "business": {
            "pending_action": result.get("pending_action", {}),
            "review_decision": result.get("review_decision", {}),
            "result": result.get("action_result", {}),
        },
        "model_provider": model_provider,
        "latency_ms": round(float(result.get("latency_ms", 0.0)), 1),
    }
