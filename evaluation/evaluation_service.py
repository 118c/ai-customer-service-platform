"""Application service for running, enriching and persisting quality evaluations."""
from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess
from typing import Any, Callable

from evaluation.full_workflow_evaluator import FullWorkflowEvaluator
from evaluation.schemas import WorkflowEvaluationCase, WorkflowEvaluationRun


class EvaluationService:
    def __init__(
        self,
        *,
        workflow: Any,
        repository: Any,
        project_root: str,
        trace_store: Any = None,
        public_response_builder: Callable[[dict[str, Any], str], Any] | None = None,
        model_provider: str | Callable[[], str] = "",
        llm_judge: Any = None,
    ):
        self.workflow = workflow
        self.repository = repository
        self.project_root = pathlib.Path(project_root)
        self.trace_store = trace_store
        self.public_response_builder = public_response_builder
        self.model_provider = model_provider
        self.llm_judge = llm_judge

    async def run(
        self, cases: list[WorkflowEvaluationCase], dataset_version: str
    ) -> WorkflowEvaluationRun:
        evaluator = FullWorkflowEvaluator(
            self.workflow,
            dataset_version=dataset_version,
            trace_store=self.trace_store,
            public_response_builder=self.public_response_builder,
            model_provider=self.model_provider,
        )
        report = await evaluator.run(cases)
        report.metadata.update(self.runtime_metadata("full-workflow"))
        await self.repository.save_run_async(report)
        return report

    async def run_rag(self, cases: list[Any], dataset_version: str) -> Any:
        from evaluation.rag_evaluator import RAGEvaluator

        evaluator = RAGEvaluator(
            self.workflow,
            llm_judge=self.llm_judge,
            dataset_version=dataset_version,
            trace_store=self.trace_store,
            model_provider=self.model_provider,
        )
        report = await evaluator.run(cases)
        report.metadata.update(self.runtime_metadata("rag"))
        await self.repository.save_run_async(report)
        return report

    def runtime_metadata(self, dataset_name: str = "full-workflow") -> dict[str, str]:
        provider = self.model_provider() if callable(self.model_provider) else self.model_provider
        provider = str(provider or "not-selected")
        return {
            "git_commit_sha": os.getenv("GIT_COMMIT_SHA", "").strip() or self._git_sha(),
            "dataset_name": dataset_name,
            "model_provider": provider,
            "model_version": self._model_version(provider),
            "prompt_skill_version": os.getenv("PROMPT_SKILL_VERSION", "").strip() or self._skills_hash(),
            "knowledge_base_version": os.getenv("KNOWLEDGE_BASE_VERSION", "enterprise_knowledge_v1"),
            "environment_type": os.getenv("APP_ENV", "development"),
        }

    @staticmethod
    def _model_version(provider: str) -> str:
        if provider == "local-continuity":
            return "local-continuity-rules-v1"
        if provider == "fallback-openai-compatible":
            return os.getenv("LLM_FALLBACK_MODEL", "not-selected")
        if provider == "primary-anthropic":
            return os.getenv("ANTHROPIC_MODEL", "not-selected")
        return "not-selected"

    def _git_sha(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.project_root,
                capture_output=True, text=True, timeout=2, check=True,
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"

    def _skills_hash(self) -> str:
        skills_root = self.project_root / "skills"
        digest = hashlib.sha256()
        files = sorted(path for path in skills_root.rglob("*") if path.is_file()) if skills_root.exists() else []
        for path in files:
            digest.update(path.relative_to(skills_root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()[:12] if files else "none"
