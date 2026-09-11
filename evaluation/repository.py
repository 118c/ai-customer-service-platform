"""SQLite repository for versioned evaluation runs and trend queries."""
from __future__ import annotations

import asyncio
import json
import pathlib
import sqlite3
from dataclasses import asdict, is_dataclass
from typing import Any


class EvaluationRepository:
    def __init__(self, database_path: str = "./data/evaluations.db"):
        self.database_path = pathlib.Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    run_id TEXT PRIMARY KEY, suite TEXT NOT NULL, dataset_version TEXT NOT NULL,
                    started_at TEXT NOT NULL, completed_at TEXT NOT NULL, status TEXT NOT NULL,
                    pass_rate REAL NOT NULL, metrics TEXT NOT NULL, metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluation_case_results (
                    run_id TEXT NOT NULL, case_id TEXT NOT NULL, passed INTEGER NOT NULL,
                    latency_ms REAL NOT NULL, checks TEXT NOT NULL, metrics TEXT NOT NULL,
                    detail TEXT NOT NULL, PRIMARY KEY (run_id, case_id),
                    FOREIGN KEY (run_id) REFERENCES evaluation_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_eval_runs_completed
                    ON evaluation_runs(completed_at DESC);
                """
            )

    def save_run(self, run: Any) -> str:
        data = asdict(run) if is_dataclass(run) else dict(run)
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO evaluation_runs
                   (run_id, suite, dataset_version, started_at, completed_at, status,
                    pass_rate, metrics, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["run_id"], data.get("suite", "full-workflow"),
                    data.get("dataset_version", "1"), data["started_at"], data["completed_at"],
                    data.get("status", "completed"), float(data.get("pass_rate", 0.0)),
                    self._json(data.get("metrics", {})), self._json(data.get("metadata", {})),
                ),
            )
            db.execute("DELETE FROM evaluation_case_results WHERE run_id = ?", (data["run_id"],))
            for item in data.get("results", []):
                result = asdict(item) if is_dataclass(item) else dict(item)
                db.execute(
                    """INSERT INTO evaluation_case_results
                       (run_id, case_id, passed, latency_ms, checks, metrics, detail)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        data["run_id"], result["case_id"], int(bool(result.get("passed"))),
                        float(result.get("latency_ms", 0.0)), self._json(result.get("checks", {})),
                        self._json(result.get("metrics", {})), result.get("detail", ""),
                    ),
                )
        return data["run_id"]

    async def save_run_async(self, run: Any) -> str:
        return await asyncio.to_thread(self.save_run, run)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM evaluation_runs ORDER BY completed_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [self._run_row(row) for row in rows]

    async def list_runs_async(self, limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.list_runs, limit)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM evaluation_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            cases = db.execute(
                "SELECT * FROM evaluation_case_results WHERE run_id = ? ORDER BY case_id", (run_id,)
            ).fetchall()
        result = self._run_row(row)
        result["results"] = [
            {
                "case_id": item["case_id"], "passed": bool(item["passed"]),
                "latency_ms": item["latency_ms"], "checks": json.loads(item["checks"]),
                "metrics": json.loads(item["metrics"]), "detail": item["detail"],
            }
            for item in cases
        ]
        return result

    async def get_run_async(self, run_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.get_run, run_id)

    def trends(self, limit: int = 30) -> list[dict[str, Any]]:
        return [
            {
                "run_id": run["run_id"], "completed_at": run["completed_at"],
                "pass_rate": run["pass_rate"], "metrics": run["metrics"],
                "dataset_version": run["dataset_version"],
            }
            for run in reversed(self.list_runs(limit))
        ]

    async def trends_async(self, limit: int = 30) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.trends, limit)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"], "suite": row["suite"],
            "dataset_version": row["dataset_version"], "started_at": row["started_at"],
            "completed_at": row["completed_at"], "status": row["status"],
            "pass_rate": row["pass_rate"], "metrics": json.loads(row["metrics"]),
            "metadata": json.loads(row["metadata"]),
        }
