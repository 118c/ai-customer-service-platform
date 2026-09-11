"""
企业员工智能客服平台 — FastAPI 入口

启动时打印小熊饼干图案。
所有核心组件在 lifespan 中初始化，通过环境变量配置。
"""
import asyncio
import logging
import os
import pathlib
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional


_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response, UploadFile, File, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
   ╔════════════════════════╗
   ║   企业员工智能客服 v3.0  ║
   ╚════════════════════════╝
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_orchestrator = None
_memory       = None
_tool_manager = None
_monitor      = None
_evaluator    = None
_skill_manager = None
_workflow = None
_llm_gateway = None
_checkpointer_context = None
_business_gateway = None
_trace_store = None
_workflow_evaluator = None
_evaluation_repository = None

def _anthropic_cfg() -> Dict[str, Any]:
    key = os.getenv("ANTHROPIC_API_KEY", "offline-continuity")
    cfg: Dict[str, Any] = {
        "api_key":  key,
        "model":    os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929").strip(),
    }
    base_url = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base_url:
        cfg["base_url"] = base_url
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator, _memory, _tool_manager, _monitor, _evaluator, _skill_manager
    global _workflow, _llm_gateway, _checkpointer_context, _business_gateway, _trace_store
    global _workflow_evaluator, _evaluation_repository

    print(BANNER, flush=True)

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from core.intent_recognizer import IntentRecognizer
    from evaluation.evaluator import EndToEndEvaluator
    from evaluation.full_workflow_evaluator import FullWorkflowEvaluator
    from evaluation.repository import EvaluationRepository
    from mcp.knowledge_base import KnowledgeBase
    from mcp.tool_manager import MCPToolManager, Tool
    from memory.conversation_memory import MemoryManager
    from monitor.performance_monitor import PerformanceMonitor
    from monitor.trace_store import TraceStore
    from core.skill_loader import SkillManager
    from core.llm_gateway import ResilientLLMGateway
    from integrations.business_services import create_business_gateway
    from orchestration.service_graph import EmployeeServiceWorkflow
    from langgraph.checkpoint.memory import InMemorySaver

    cfg = _anthropic_cfg()
    logger.info(f"模型: {cfg['model']}  base_url: {cfg.get('base_url', '(官方)')}")
    _llm_gateway = ResilientLLMGateway.from_env()

    # 意图识别器（Orchestrator 内部也会创建，这里单独暴露给 Evaluator）
    recognizer = IntentRecognizer(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        llm_gateway=_llm_gateway,
    )

    # Skills：启动时从目录加载业务能力说明，并在 Agent 调用 LLM 时动态注入。
    skills_dir = os.getenv("SERVICE_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills"))
    _skill_manager = SkillManager(
        root_dir=skills_dir,
        max_prompt_chars=int(os.getenv("SERVICE_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    _skill_manager.load()

    # Agent 编排器
    _orchestrator = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=_skill_manager,
        llm_gateway=_llm_gateway,
    )

    # 记忆管理器（Redis 工作记忆 + ChromaDB 情景记忆/用户画像）
    _memory = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        llm_gateway=_llm_gateway,
    )

    # MCP 工具管理器 + RAG 知识库（基于 ChromaDB 的真实检索）
    _tool_manager = MCPToolManager(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        llm_gateway=_llm_gateway,
    )
    kb = KnowledgeBase(
        chroma_host=os.getenv("CHROMA_HOST", "chromadb"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/app/data/chroma"),
    )
    logger.info(f"知识库已加载: {await kb.doc_count_async()} 个文档片段")

    def knowledge_fallback(params: Dict[str, Any], context: Optional[Dict[str, Any]], error: str):
        query = params.get("query", "")
        return [{
            "title": "知识库降级结果",
            "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试，或转人工客服确认。",
            "score": 0.0,
            "fallback": True,
            "error": error,
        }]

    _tool_manager.register(Tool(
        name="knowledge_search",
        description="搜索知识库（基于 ChromaDB 向量检索）",
        handler=kb.search_handler,
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
        cache_ttl=300.0,
        supports_rerank=True,
        fallback=knowledge_fallback,
    ))

    # LangGraph 检查点优先持久化到 Redis；连接或索引初始化失败时保持服务可用，
    # 并在健康接口明确标记为内存级连续性。
    checkpointer = None
    checkpoint_backend = "memory"
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        checkpoint_url = os.getenv("LANGGRAPH_REDIS_URL", os.getenv("REDIS_URL", "redis://redis:6379/1"))
        _checkpointer_context = AsyncRedisSaver.from_conn_string(
            checkpoint_url,
            ttl={"default_ttl": int(os.getenv("LANGGRAPH_CHECKPOINT_TTL_MINUTES", "1440")),
                 "refresh_on_read": True},
        )
        checkpointer = await _checkpointer_context.__aenter__()
        await checkpointer.asetup()
        checkpoint_backend = "redis"
    except Exception as ex:
        logger.warning(f"Redis 检查点不可用，使用进程内检查点: {ex}")
        checkpointer = InMemorySaver()
        _checkpointer_context = None

    _business_gateway = create_business_gateway()
    _trace_store = TraceStore(
        os.getenv("TRACE_REDIS_URL", os.getenv("REDIS_URL", "redis://redis:6379/0")),
        ttl_seconds=int(os.getenv("TRACE_TTL_SECONDS", "86400")),
    )
    _workflow = EmployeeServiceWorkflow(
        orchestrator=_orchestrator,
        memory=_memory,
        tool_manager=_tool_manager,
        business_gateway=_business_gateway,
        checkpointer=checkpointer,
    )
    app.state.checkpoint_backend = checkpoint_backend
    _workflow_evaluator = FullWorkflowEvaluator(
        _workflow,
        dataset_version=os.getenv("WORKFLOW_EVAL_DATASET_VERSION", "2026.09"),
    )
    _evaluation_repository = EvaluationRepository(
        os.getenv("EVALUATION_DB_PATH", "./data/evaluations.db")
    )

    # 性能监控（可选启动 Prometheus）
    prom_port = int(os.getenv("PROMETHEUS_PORT", "0")) or None
    _monitor = PerformanceMonitor(
        orchestrator=_orchestrator,
        tool_manager=_tool_manager,
        interval_s=float(os.getenv("MONITOR_INTERVAL", "10")),
        webhook_url=os.getenv("ALERT_WEBHOOK_URL") or None,
        prometheus_port=prom_port,
    )
    await _monitor.start()

    # 评测器
    _evaluator = EndToEndEvaluator(
        orchestrator=_orchestrator,
        recognizer=recognizer,
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        baseline_path=os.getenv("EVAL_BASELINE_PATH", "/app/data/eval/baseline.json"),
        llm_gateway=_llm_gateway,
    )

    logger.info("员工智能客服已就绪")
    yield

    await _monitor.stop()
    if _memory is not None:
        await _memory.close()
    if _trace_store is not None:
        await _trace_store.close()
    if _checkpointer_context is not None:
        await _checkpointer_context.__aexit__(None, None, None)
    logger.info("员工智能客服已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="企业员工智能客服",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174").split(",") if item.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: FastAPIRequest, call_next):
    """Attach a correlation id and optionally enforce the deployment API key."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    configured_key = os.getenv("APP_API_KEY", "").strip()
    public_paths = {"/health", "/metrics", "/openapi.json", "/docs", "/redoc"}
    if configured_key and request.url.path not in public_paths:
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        supplied = request.headers.get("X-API-Key", "") or bearer
        if not secrets.compare_digest(supplied, configured_key):
            return Response(
                content='{"detail":"unauthorized"}',
                status_code=401,
                media_type="application/json",
                headers={"X-Request-ID": request_id},
            )
    started = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        elapsed_ms = (time.monotonic() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        return response
    finally:
        if _monitor is not None:
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            _monitor.record_request(request.method, route_path, status_code, (time.monotonic() - started) * 1000)


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message:     str = Field(min_length=1, max_length=4000)
    user_id:     str = Field(default="anonymous", min_length=1, max_length=64, pattern=r"^[\w.-]+$")
    conv_id:     Optional[str] = Field(default=None, max_length=128, pattern=r"^[\w.-]+$")


class SourceReference(BaseModel):
    title: str
    excerpt: str = ""


class ConfirmationField(BaseModel):
    label: str
    value: str


class ActionConfirmation(BaseModel):
    title: str
    description: str
    fields: List[ConfirmationField] = Field(default_factory=list)
    confirm_label: str = "确认提交"
    cancel_label: str = "暂不提交"


class BusinessReceipt(BaseModel):
    record_id: str
    status: str
    message: str


class ChatResponse(BaseModel):
    """Stable employee-facing contract without orchestration internals."""
    request_id: str
    conversation_id: str
    answer: str
    status: str = "completed"
    sources: List[SourceReference] = Field(default_factory=list)
    confirmation: Optional[ActionConfirmation] = None
    receipt: Optional[BusinessReceipt] = None


class ConfirmationRequest(BaseModel):
    action: str = Field(pattern="^(confirm|cancel)$")
    changes: Optional[Dict[str, Any]] = None


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {"status": "ok"}


@app.get("/admin/status", tags=["Operations"])
async def admin_status(request: FastAPIRequest):
    """Return service internals to authorized operators, never to the employee client."""
    _require_admin(request)
    if _orchestrator is None:
        raise HTTPException(503, "服务未就绪")
    return {
        "status": "ok",
        "agents": _orchestrator.get_stats(),
        "models": _llm_gateway.get_stats() if _llm_gateway else {},
        "checkpoint_backend": getattr(app.state, "checkpoint_backend", "unknown"),
    }


@app.get("/skills", tags=["Skills"])
async def skills_summary(request: FastAPIRequest):
    """查看当前已加载的 Skills，便于确认热加载结果和排查解析错误。"""
    _require_admin(request)
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    return _skill_manager.summary()


@app.post("/skills/reload", tags=["Skills"])
async def reload_skills(request: FastAPIRequest):
    """运行时重新扫描 Skill 目录，不需要重启服务。"""
    _require_admin(request)
    if _skill_manager is None:
        raise HTTPException(503, "Skills 未初始化")
    _skill_manager.reload()
    if _orchestrator is not None:
        _orchestrator.set_skill_manager(_skill_manager)
    return _skill_manager.summary()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """
    员工端统一自然语言入口。内部编排细节不会进入公开响应。
    """
    if _workflow is None:
        raise HTTPException(503, "服务未就绪")
    conv_id = req.conv_id or str(uuid.uuid4())
    request_id = uuid.uuid4().hex
    result = await _workflow.invoke({
        "request_id": request_id,
        "user_id": req.user_id,
        "conversation_id": conv_id,
        "message": req.message,
        "started_at": time.time(),
    })
    await _save_trace(result)
    if result.get("status") == "completed":
        asyncio.create_task(_memory.update_profile(req.user_id, conv_id))
    return _employee_response(result, conv_id)


@app.post("/requests/{request_id}/confirm", response_model=ChatResponse, tags=["Employee Service"])
async def confirm_action(request_id: str, body: ConfirmationRequest):
    """Confirm or cancel a business action prepared through the conversation."""
    if _workflow is None:
        raise HTTPException(503, "服务未就绪")
    decision: Dict[str, Any] = {
        "decision": "approve" if body.action == "confirm" else "reject",
        "reason": "employee_confirmed" if body.action == "confirm" else "employee_cancelled",
    }
    if body.changes:
        decision.update({"decision": "edit", "payload": body.changes})
    result = await _workflow.resume(request_id, decision)
    await _save_trace(result)
    if result.get("status") == "completed":
        asyncio.create_task(_memory.update_profile(result.get("user_id", "anonymous"), result.get("conversation_id", "")))
    return _employee_response(result, result.get("conversation_id", ""))


@app.get("/admin/traces/{request_id}", tags=["Operations"])
async def request_trace(request_id: str, request: FastAPIRequest):
    """Return internal orchestration telemetry to authorized operators."""
    _require_admin(request)
    if _trace_store is None:
        raise HTTPException(503, "追踪存储未初始化")
    trace = await _trace_store.get(request_id)
    if trace is None:
        raise HTTPException(404, "未找到请求追踪")
    return trace


def _employee_response(result: Dict[str, Any], conv_id: str) -> ChatResponse:
    raw_status = result.get("status", "completed")
    confirmation = _confirmation_from_result(result) if raw_status == "awaiting_review" else None
    receipt = _receipt_from_result(result)
    return ChatResponse(
        request_id=result.get("request_id", ""),
        conversation_id=conv_id,
        answer=result.get("response", ""),
        status="confirmation_required" if confirmation else "completed",
        sources=[
            SourceReference(title=str(item.get("title", "内部资料")), excerpt=str(item.get("excerpt", "")))
            for item in result.get("sources", []) if isinstance(item, dict)
        ],
        confirmation=confirmation,
        receipt=receipt,
    )


def _confirmation_from_result(result: Dict[str, Any]) -> Optional[ActionConfirmation]:
    interrupts = result.get("interrupts", [])
    if not interrupts or not isinstance(interrupts[0], dict):
        return None
    event = interrupts[0]
    action_type = event.get("action_type", "")
    payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
    definitions = {
        "repair.create": ("确认提交设备报修", [
            ("设备编号", "equipment_id"), ("所在区域", "area"), ("故障描述", "symptom"),
        ]),
        "workflow.submit": ("确认提交流程申请", [("申请内容", "title")]),
    }
    title, field_defs = definitions.get(action_type, ("确认提交业务申请", []))
    fields = [
        ConfirmationField(label=label, value=str(payload.get(key, "未填写") or "未填写"))
        for label, key in field_defs
    ]
    return ActionConfirmation(
        title=title,
        description="请核对以下信息。确认后将提交至业务系统。",
        fields=fields,
    )


def _receipt_from_result(result: Dict[str, Any]) -> Optional[BusinessReceipt]:
    action_result = result.get("action_result", {})
    if not isinstance(action_result, dict) or not action_result.get("success"):
        return None
    record_id = str(action_result.get("record_id", ""))
    status = str(action_result.get("status", "submitted"))
    return BusinessReceipt(
        record_id=record_id,
        status=status,
        message=f"申请已受理，编号 {record_id}",
    )


async def _save_trace(result: Dict[str, Any]) -> None:
    if _trace_store is None or not result.get("request_id"):
        return
    from monitor.trace_store import build_trace
    await _trace_store.save(
        result["request_id"],
        build_trace(result, _llm_gateway.last_provider if _llm_gateway else ""),
    )


def _require_admin(request: FastAPIRequest) -> None:
    configured = os.getenv("ADMIN_API_KEY", "").strip()
    if configured:
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        supplied = request.headers.get("X-Admin-Key", "") or bearer
        if not secrets.compare_digest(supplied, configured):
            raise HTTPException(403, "管理员凭证无效")
        return
    if (request.client.host if request.client else "") not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(403, "管理接口仅允许本机访问；生产环境请配置 ADMIN_API_KEY")


async def _build_knowledge_context(message: str, intent=None, top_k: int = 3) -> tuple[str, bool]:
    """
    为 /chat 主链路构建 RAG 知识上下文。

    这里复用 MCPToolManager 的查询改写、并行召回、重排、fallback 能力。
    """
    if _tool_manager is None:
        return "", False
    if not _should_use_knowledge(message, intent=intent):
        return "", False
    try:
        result = await _tool_manager.search_with_rewrite("knowledge_search", message, top_k=top_k)
        if not result.success or not isinstance(result.data, list) or not result.data:
            return "", False

        parts = ["[知识库检索结果]"]
        used = False
        for i, item in enumerate(result.data[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "未命名文档"))
            content = str(item.get("content", "")).strip()
            score = item.get("score", "")
            if not content:
                continue
            used = True
            parts.append(f"{i}. 标题: {title}\n   相关度: {score}\n   内容: {content[:600]}")

        if not used:
            return "", False
        parts.append("请优先依据以上知识库内容回答；如果知识库内容不足，再结合通用客服能力说明。")
        return "\n".join(parts), True
    except Exception as ex:
        logger.warning(f"构建知识库上下文失败: {ex}")
        return "", False


def _should_use_knowledge(message: str, intent=None) -> bool:
    """跳过纯寒暄，业务类问题才检索知识库，避免无关 RAG 干扰回复。"""
    msg = (message or "").strip().lower()
    if not msg:
        return False
    intent_value = getattr(intent, "value", intent)
    if intent_value in {"greeting", "feedback", "escalation", "human_handoff", "other"}:
        return False
    if intent_value in {
        "query", "request", "technical", "policy", "workflow", "complaint",
        "process_spec", "attendance_query", "payroll_query", "equipment_fault",
        "repair_request", "workflow_query", "approval_request",
    }:
        return True
    greetings = {"你好", "您好", "嗨", "hi", "hello", "hey", "早上好", "晚上好"}
    if msg in greetings:
        return False
    business_keywords = [
        "工艺", "规范", "考勤", "打卡", "请假", "工资", "补贴", "设备", "机台",
        "故障", "报修", "维修", "流程", "审批", "申请单", "停机", "sop", "error",
    ]
    return len(msg) >= 4 or any(kw in msg for kw in business_keywords)


@app.get("/monitor")
async def monitor_summary(request: FastAPIRequest):
    """实时监控摘要：Agent 成功率、工具统计、告警、优化建议。"""
    _require_admin(request)
    if _monitor is None:
        raise HTTPException(503, "服务未就绪")
    summary = _monitor.summary()
    summary["model_stats"] = _llm_gateway.get_stats() if _llm_gateway else {}
    summary["checkpoint_backend"] = getattr(app.state, "checkpoint_backend", "unknown")
    return summary


@app.get("/operations/tasks", tags=["Operations"])
async def operations_tasks(request: FastAPIRequest, limit: int = 20):
    """Return recent repair, workflow and human-collaboration tasks."""
    _require_admin(request)
    if _business_gateway is None:
        raise HTTPException(503, "业务连接器未初始化")
    return {"items": await _business_gateway.list_tasks(limit)}


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(request: FastAPIRequest, query: str, top_k: int = 5):
    """
    执行检索优化链路：查询改写 → 并行召回 → 重排 → Top-K。
    """
    _require_admin(request)
    if _tool_manager is None:
        raise HTTPException(503, "服务未就绪")
    result = await _tool_manager.search_with_rewrite("knowledge_search", query, top_k=top_k)
    return {"query": query, "results": result.data, "reranked": result.reranked}


class DocInput(BaseModel):
    """单篇文档输入。"""
    title:   str
    content: str


class BatchDocInput(BaseModel):
    """批量文档导入请求体。"""
    documents: List[DocInput]


class EvalIntentInput(BaseModel):
    """意图识别评测用例。"""
    message: str
    expected_intent: str
    context: Optional[Dict[str, Any]] = None


class EvalDialogInput(BaseModel):
    """对话质量评测用例。question 单轮，turns 多轮。"""
    question: Optional[str] = None
    turns: Optional[List[str]] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None


class EvalRunInput(BaseModel):
    """评测请求。为空时使用内置默认用例。"""
    intent_cases: Optional[List[EvalIntentInput]] = None
    dialog_cases: Optional[List[EvalDialogInput]] = None


class WorkflowEvalCaseInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    expected_intent: Optional[str] = None
    expected_action_type: Optional[str] = None
    expected_final_status: str = "completed"
    decision: Optional[str] = Field(default=None, pattern="^(confirm|cancel|approve|reject)$")
    relevant_documents: List[str] = Field(default_factory=list)
    expected_facts: List[str] = Field(default_factory=list)
    expect_receipt: bool = False
    verify_idempotency: bool = False
    expected_degradation_components: List[str] = Field(default_factory=list)
    user_id: str = "E1001"


class WorkflowEvalRunInput(BaseModel):
    dataset_version: str = Field(default="2026.09", min_length=1, max_length=64)
    cases: Optional[List[WorkflowEvalCaseInput]] = None


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput, request: FastAPIRequest):
    """
    批量导入文档到知识库。

    文档会自动切片（每片 500 字）并存入 ChromaDB，ChromaDB 内置 Embedding 模型自动向量化。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "夜班考勤规则", "content": "跨日夜班以班次开始日期归属考勤日..."},
        {"title": "设备报修规范", "content": "P1 停线故障需立即通知值班工程师..."}
      ]
    }
    ```
    """
    _require_admin(request)
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    count = await kb.add_documents_async([{"title": d.title, "content": d.content} for d in body.documents])
    total = await kb.doc_count_async()
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": total}


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(request: FastAPIRequest, file: UploadFile = File(...)):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    _require_admin(request)
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json
        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = await kb.add_documents_async(docs)
    total = await kb.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats(request: FastAPIRequest):
    """查看知识库统计信息（文档片段总数）。"""
    _require_admin(request)
    tool = _tool_manager._tools.get("knowledge_search") if _tool_manager else None
    if tool is None:
        raise HTTPException(503, "知识库未初始化")
    kb = tool.handler.__self__
    return {"total_chunks": await kb.doc_count_async()}


@app.post("/eval/run")
async def run_eval(request: FastAPIRequest, body: Optional[EvalRunInput] = None):
    """运行内置评测用例，返回评测报告。"""
    _require_admin(request)
    if _evaluator is None:
        raise HTTPException(503, "服务未就绪")
    from evaluation.evaluator import DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES, IntentTestCase

    if body and body.intent_cases is not None:
        intent_cases = [
            IntentTestCase(
                message=c.message,
                expected_intent=c.expected_intent,
                context=c.context,
            )
            for c in body.intent_cases
        ]
    else:
        dataset_path = pathlib.Path(_ROOT) / "evaluation" / "datasets" / "factory_intents.jsonl"
        if dataset_path.exists():
            import json
            intent_cases = [
                IntentTestCase(row["message"], row["expected_intent"])
                for row in (
                    json.loads(line)
                    for line in dataset_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            ]
        else:
            intent_cases = DEFAULT_INTENT_CASES

    if body and body.dialog_cases is not None:
        dialog_cases = [
            c.model_dump(exclude_none=True)
            for c in body.dialog_cases
        ]
    else:
        dialog_cases = DEFAULT_DIALOG_CASES

    report = await _evaluator.run(
        intent_cases=intent_cases,
        dialog_cases=dialog_cases,
    )
    return {
        "pass_rate":       report.pass_rate,
        "total":           report.total,
        "passed":          report.passed,
        "avg_scores":      report.avg_scores,
        "regressions":     report.regressions,
        "recommendations": report.recommendations,
        "results": [
            {
                "test_id": r.test_id,
                "passed": r.passed,
                "scores": r.scores,
                "detail": r.detail,
                "metadata": r.metadata,
            }
            for r in report.results
        ],
    }


def _default_workflow_eval_cases():
    import json
    from evaluation.full_workflow_evaluator import WorkflowEvaluationCase
    dataset_path = pathlib.Path(_ROOT) / "evaluation" / "datasets" / "workflow_cases.jsonl"
    if dataset_path.exists():
        return [
            WorkflowEvaluationCase(**json.loads(line))
            for line in dataset_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return [
        WorkflowEvaluationCase(
            case_id="policy-rag-attendance",
            message="跨日夜班的考勤日期怎么计算？",
            expected_intent="attendance_query",
            relevant_documents=["三班制考勤与交接规范"],
            expected_facts=["班次开始日期"],
        ),
        WorkflowEvaluationCase(
            case_id="workflow-status-query",
            message="申请单WF-202609-001现在到哪个节点？",
            expected_intent="workflow_query",
        ),
        WorkflowEvaluationCase(
            case_id="approval-cancel",
            message="帮我提交一份领料申请",
            expected_intent="approval_request",
            expected_action_type="workflow.submit",
            decision="cancel",
        ),
        WorkflowEvaluationCase(
            case_id="repair-confirm-idempotency",
            message="帮我给设备EQ-A17创建报修单，主轴有异响",
            expected_intent="repair_request",
            expected_action_type="repair.create",
            decision="confirm",
            expect_receipt=True,
            verify_idempotency=True,
        ),
    ]


@app.post("/admin/evaluations/run", tags=["Operations"])
async def run_workflow_evaluation(
    request: FastAPIRequest, body: Optional[WorkflowEvalRunInput] = None
):
    """Run the versioned LangGraph/RAG/business/HITL regression suite."""
    _require_admin(request)
    if _workflow is None or _evaluation_repository is None:
        raise HTTPException(503, "评测服务未就绪")
    from evaluation.full_workflow_evaluator import FullWorkflowEvaluator, WorkflowEvaluationCase

    dataset_version = body.dataset_version if body else "2026.09"
    if body and body.cases is not None:
        cases = [WorkflowEvaluationCase(**case.model_dump()) for case in body.cases]
    else:
        cases = _default_workflow_eval_cases()
    evaluator = FullWorkflowEvaluator(_workflow, dataset_version=dataset_version)
    report = await evaluator.run(cases)
    await _evaluation_repository.save_run_async(report)
    return report.to_dict()


@app.get("/admin/evaluations", tags=["Operations"])
async def list_workflow_evaluations(request: FastAPIRequest, limit: int = 20):
    _require_admin(request)
    if _evaluation_repository is None:
        raise HTTPException(503, "评测存储未就绪")
    return {"items": await _evaluation_repository.list_runs_async(limit)}


@app.get("/admin/evaluations/trends", tags=["Operations"])
async def workflow_evaluation_trends(request: FastAPIRequest, limit: int = 30):
    _require_admin(request)
    if _evaluation_repository is None:
        raise HTTPException(503, "评测存储未就绪")
    return {"items": await _evaluation_repository.trends_async(limit)}


@app.get("/admin/evaluations/{run_id}", tags=["Operations"])
async def get_workflow_evaluation(run_id: str, request: FastAPIRequest):
    _require_admin(request)
    if _evaluation_repository is None:
        raise HTTPException(503, "评测存储未就绪")
    report = await _evaluation_repository.get_run_async(run_id)
    if report is None:
        raise HTTPException(404, "未找到评测记录")
    return report


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def _cli():
    print(BANNER)
    print("员工智能客服 CLI — 输入 quit 退出\n")

    from agents.agent_orchestrator import AgentOrchestrator, Request
    from memory.conversation_memory import MemoryManager, MsgRole
    from core.skill_loader import SkillManager

    cfg = _anthropic_cfg()
    skill_manager = SkillManager(
        root_dir=os.getenv("SERVICE_SKILLS_DIR", str(pathlib.Path(_ROOT) / "skills")),
        max_prompt_chars=int(os.getenv("SERVICE_SKILLS_MAX_PROMPT_CHARS", "5000")),
    )
    skill_manager.load()
    orch = AgentOrchestrator(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
        skill_manager=skill_manager,
    )
    mem  = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        chroma_host=os.getenv("CHROMA_HOST", "localhost"),
        chroma_port=int(os.getenv("CHROMA_PORT", "8000")),
        chroma_path=os.getenv("CHROMA_PERSIST_DIRECTORY", "/tmp/chroma"),
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        model=cfg["model"],
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = [
            {"role": m.role.value, "content": m.content}
            for m in ctx.recent_messages[-5:]
        ] if ctx.recent_messages else None
        req = Request(message=msg, user_id=user_id, conv_id=conv_id, context=ctx.to_prompt_text(), history=history)
        result = await orch.run(req)

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, result.response)

        print(f"\n智能客服: {result.response}\n")

    await mem.close()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        asyncio.run(_cli())
    else:
        uvicorn.run(
            "api.main:app",
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            reload=os.getenv("APP_ENV") == "development",
        )
    _llm_gateway = ResilientLLMGateway.from_env()
