"""Controlled dependency-failure adapters used by reliability evaluations."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class FaultRule:
    operation: str
    mode: str = "error"
    remaining: int = 1
    delay_s: float = 0.01


class InjectedDependencyError(RuntimeError):
    pass


class _FaultController:
    def __init__(self, rules: list[FaultRule] | None = None):
        self.rules = rules or []

    async def apply(self, operation: str) -> None:
        for rule in self.rules:
            if rule.operation != operation or rule.remaining <= 0:
                continue
            rule.remaining -= 1
            if rule.mode == "timeout":
                await asyncio.sleep(max(0.0, rule.delay_s))
                raise TimeoutError(f"controlled timeout: {operation}")
            if rule.mode == "rate_limit":
                raise InjectedDependencyError(f"controlled rate limit: {operation}")
            raise InjectedDependencyError(f"controlled failure: {operation}")


class FaultInjectingBusinessGateway:
    def __init__(self, delegate: Any, rules: list[FaultRule] | None = None):
        self.delegate = delegate
        self.controller = _FaultController(rules)
        self.query_calls = 0
        self.execute_calls = 0

    async def query(self, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.query_calls += 1
        await self.controller.apply("business.query")
        return await self.delegate.query(action_type, payload)

    async def execute(self, action: Any) -> dict[str, Any]:
        self.execute_calls += 1
        await self.controller.apply("business.execute")
        return await self.delegate.execute(action)

    async def list_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        await self.controller.apply("business.list_tasks")
        return await self.delegate.list_tasks(limit)


class FaultInjectingToolManager:
    def __init__(self, delegate: Any, rules: list[FaultRule] | None = None):
        self.delegate = delegate
        self.controller = _FaultController(rules)

    async def search_with_rewrite(self, *args: Any, **kwargs: Any) -> Any:
        await self.controller.apply("knowledge.search")
        return await self.delegate.search_with_rewrite(*args, **kwargs)

    def get_stats(self) -> dict[str, Any]:
        return self.delegate.get_stats()


class FaultInjectingMemory:
    def __init__(self, delegate: Any, rules: list[FaultRule] | None = None):
        self.delegate = delegate
        self.controller = _FaultController(rules)

    async def get_context(self, *args: Any, **kwargs: Any) -> Any:
        await self.controller.apply("memory.read")
        return await self.delegate.get_context(*args, **kwargs)

    async def add_message(self, *args: Any, **kwargs: Any) -> Any:
        await self.controller.apply("memory.write")
        return await self.delegate.add_message(*args, **kwargs)


class FaultInjectingOrchestrator:
    def __init__(self, delegate: Any, rules: list[FaultRule] | None = None):
        self.delegate = delegate
        self.controller = _FaultController(rules)

    async def recognize_intent(self, *args: Any, **kwargs: Any) -> Any:
        await self.controller.apply("intent.recognize")
        return await self.delegate.recognize_intent(*args, **kwargs)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        await self.controller.apply("agent.execute")
        return await self.delegate.run(*args, **kwargs)
