"""Ports and adapters for attendance, maintenance, workflow and handoff systems."""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol

import httpx


@dataclass(frozen=True)
class BusinessAction:
    action_type: str
    payload: Dict[str, Any]
    requires_review: bool
    description: str


class BusinessServiceGateway(Protocol):
    async def query(self, action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]: ...
    async def execute(self, action: BusinessAction) -> Dict[str, Any]: ...
    async def list_tasks(self, limit: int = 20) -> list[Dict[str, Any]]: ...


class LocalReferenceGateway:
    """Runnable reference connector backed by SQLite and seeded enterprise records."""

    def __init__(self, database_path: str = "./data/reference-services.db"):
        self.database_path = pathlib.Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS attendance (
                    employee_id TEXT NOT NULL, work_date TEXT NOT NULL, shift TEXT NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY (employee_id, work_date)
                );
                CREATE TABLE IF NOT EXISTS repair_orders (
                    id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL, area TEXT,
                    symptom TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, current_node TEXT NOT NULL,
                    owner TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS handoff_tasks (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    reason TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY, action_type TEXT NOT NULL, payload TEXT NOT NULL,
                    result TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO attendance VALUES (?, ?, ?, ?)",
                ("E1001", datetime.now(timezone.utc).date().isoformat(), "B", "normal"),
            )
            db.execute(
                "INSERT OR IGNORE INTO repair_orders VALUES (?, ?, ?, ?, ?, ?)",
                ("REP-20260910-001", "EQ-A17", "A3", "主轴异响", "assigned", self._now()),
            )
            db.execute(
                "INSERT OR IGNORE INTO workflows VALUES (?, ?, ?, ?, ?, ?)",
                ("WF-202609-001", "设备停机审批", "部门主管", "E2031", "processing", self._now()),
            )

    async def query(self, action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.to_thread(self._query_sync, action_type, payload)

    def _query_sync(self, action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        table_and_key = {
            "attendance.lookup": ("attendance", "employee_id", payload.get("employee_id", "E1001")),
            "repair.lookup": ("repair_orders", "id", payload.get("repair_id", "REP-20260910-001")),
            "workflow.lookup": ("workflows", "id", payload.get("workflow_id", "WF-202609-001")),
        }
        if action_type not in table_and_key:
            return {"found": False, "reason": "unsupported_query", "action_type": action_type}
        table, column, value = table_and_key[action_type]
        with self._connect() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE {column} = ? LIMIT 1", (value,)).fetchone()
        return {"found": row is not None, "record": dict(row) if row else None, "source": "local-reference"}

    async def execute(self, action: BusinessAction) -> Dict[str, Any]:
        return await asyncio.to_thread(self._execute_sync, action)

    def _execute_sync(self, action: BusinessAction) -> Dict[str, Any]:
        now = self._now()
        if action.action_type == "repair.create":
            record_id = f"REP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            with self._connect() as db:
                db.execute(
                    "INSERT INTO repair_orders VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, action.payload.get("equipment_id", "UNASSIGNED"), action.payload.get("area", ""),
                     action.payload.get("symptom", "待补充"), "submitted", now),
                )
            result = {"success": True, "record_id": record_id, "status": "submitted"}
            self._audit(action, result)
            return result
        if action.action_type == "workflow.submit":
            record_id = f"WF-{datetime.now().strftime('%Y%m')}-{uuid.uuid4().hex[:6].upper()}"
            with self._connect() as db:
                db.execute(
                    "INSERT INTO workflows VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, action.payload.get("title", "员工服务申请"), "直属主管",
                     action.payload.get("user_id", "unknown"), "processing", now),
                )
            result = {"success": True, "record_id": record_id, "status": "processing"}
            self._audit(action, result)
            return result
        if action.action_type == "handoff.create":
            record_id = f"HITL-{uuid.uuid4().hex[:10].upper()}"
            with self._connect() as db:
                db.execute(
                    "INSERT INTO handoff_tasks VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, action.payload.get("user_id", "unknown"),
                     action.payload.get("conversation_id", "unknown"),
                     action.payload.get("reason", "人工协助"), "pending", now),
                )
            result = {"success": True, "record_id": record_id, "status": "pending"}
            self._audit(action, result)
            return result
        return {"success": False, "reason": "unsupported_action", "action": asdict(action)}

    async def list_tasks(self, limit: int = 20) -> list[Dict[str, Any]]:
        return await asyncio.to_thread(self._list_tasks_sync, max(1, min(limit, 100)))

    def _list_tasks_sync(self, limit: int) -> list[Dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, 'repair' AS task_type, status, created_at AS updated_at FROM repair_orders
                UNION ALL
                SELECT id, 'workflow' AS task_type, status, updated_at FROM workflows
                UNION ALL
                SELECT id, 'handoff' AS task_type, status, created_at AS updated_at FROM handoff_tasks
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _audit(self, action: BusinessAction, result: Dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, action.action_type, json.dumps(action.payload, ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False), self._now()),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


class HttpEnterpriseGateway:
    """Connector for existing enterprise APIs using a stable internal contract."""

    def __init__(self, base_url: str, api_token: str, timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout_s = timeout_s

    async def query(self, action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/v1/queries", {"type": action_type, "payload": payload})

    async def execute(self, action: BusinessAction) -> Dict[str, Any]:
        return await self._request("POST", "/v1/actions", {**asdict(action), "approved": True})

    async def list_tasks(self, limit: int = 20) -> list[Dict[str, Any]]:
        result = await self._request("POST", "/v1/tasks/query", {"limit": max(1, min(limit, 100))})
        return list(result.get("items", []))

    async def _request(self, method: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_token}", "Idempotency-Key": uuid.uuid4().hex}
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.request(method, f"{self.base_url}{path}", headers=headers, json=payload)
            response.raise_for_status()
        return response.json()


def create_business_gateway() -> BusinessServiceGateway:
    base_url = os.getenv("ENTERPRISE_API_BASE_URL", "").strip()
    token = os.getenv("ENTERPRISE_API_TOKEN", "").strip()
    if base_url and token:
        return HttpEnterpriseGateway(base_url, token, float(os.getenv("ENTERPRISE_API_TIMEOUT_SECONDS", "5")))
    return LocalReferenceGateway(os.getenv("REFERENCE_SERVICE_DB", "./data/reference-services.db"))


def plan_business_action(intent: str, message: str, user_id: str, conversation_id: str,
                         entities: Optional[Dict[str, list[str]]] = None) -> Optional[BusinessAction]:
    entities = entities or {}
    first = lambda key, default="": (entities.get(key) or [default])[0]
    if intent == "repair_request":
        return BusinessAction(
            "repair.create",
            {"equipment_id": first("equipment_id", "UNASSIGNED"), "area": "", "symptom": message,
             "user_id": user_id},
            True,
            "创建设备报修单",
        )
    if intent == "approval_request":
        return BusinessAction(
            "workflow.submit",
            {"title": message[:80], "user_id": user_id, "conversation_id": conversation_id},
            True,
            "提交企业流程申请",
        )
    if intent in {"human_handoff", "escalation"}:
        return BusinessAction(
            "handoff.create",
            {"reason": message[:200], "user_id": user_id, "conversation_id": conversation_id},
            False,
            "创建人工协同任务",
        )
    return None


def plan_business_query(intent: str, entities: Optional[Dict[str, list[str]]] = None) -> Optional[tuple[str, Dict[str, Any]]]:
    entities = entities or {}
    if intent in {"attendance_query", "payroll_query"}:
        return "attendance.lookup", {"employee_id": (entities.get("employee_id") or ["E1001"])[0]}
    if intent == "workflow_query":
        return "workflow.lookup", {"workflow_id": (entities.get("workflow_id") or ["WF-202609-001"])[0]}
    return None
