"""
RAG 知识库 —— 基于 ChromaDB 的真实检索实现。

功能：
  1. 文档导入：将文本切片后存入 ChromaDB（本地生成 Embedding）
  2. 语义检索：根据 query 从知识库中检索最相关的文档片段
  3. 与 MCP 工具框架集成：作为 knowledge_search 工具的真实 handler

ChromaDB 在这里的角色：
  - memory/ 中用于存储对话记忆（情景记忆 + 用户画像）
  - 这里用于存储知识库文档（RAG 检索）
  两者是不同的 collection，互不干扰。
"""
import asyncio
import hashlib
import logging
from typing import Any, Dict, List, Optional

import chromadb
from core.embedding import HashEmbeddingFunction, local_data_path

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    基于 ChromaDB 的 RAG 知识库。

    集合绑定无外部依赖的字符 n-gram 嵌入函数，调用 add() 和 query()
    时自动生成向量，不需要下载模型或调用外部 Embedding API。
    """

    COLLECTION_NAME = "enterprise_knowledge_v2"

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        chroma_path: str = "./data/chroma",
    ):
        # 优先连接独立 ChromaDB 服务（服务端内置 embedding 模型，客户端无需下载）
        self._use_server = False
        try:
            # HttpClient 默认也会初始化 ChromaDB telemetry；显式关闭避免 posthog 兼容性错误日志。
            self._client = chromadb.HttpClient(
                host=chroma_host,
                port=chroma_port,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
            self._client.heartbeat()
            self._use_server = True
            logger.info(f"知识库 ChromaDB 已连接: {chroma_host}:{chroma_port}")
        except Exception:
            chroma_path = local_data_path(chroma_path)
            logger.info(f"知识库 ChromaDB 服务不可用，使用本地模式: {chroma_path}")
            self._client = chromadb.PersistentClient(
                path=chroma_path,
                settings=chromadb.Settings(anonymized_telemetry=False),
            )

        # 服务端与本地模式使用同一确定性嵌入函数，便于跨环境复现。
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "企业员工服务知识库"},
            embedding_function=HashEmbeddingFunction(),
        )

        # 如果知识库为空，导入默认文档
        if self._collection.count() == 0:
            self._load_default_docs()

    # ── 文档管理 ──────────────────────────────────────────────────────────────

    def add_documents(self, documents: List[Dict[str, str]]) -> int:
        """
        批量导入文档到知识库。

        documents 格式: [{"title": "...", "content": "..."}, ...]
        长文档会自动切片（每片 500 字）。
        """
        ids, docs, metas = [], [], []

        for doc in documents:
            title   = doc.get("title", "")
            content = doc.get("content", "")
            document_id = doc.get("document_id") or hashlib.sha256(
                f"{title}:{content}".encode("utf-8")
            ).hexdigest()[:20]
            version = doc.get("version", "1")
            effective_date = doc.get("effective_date", "")
            chunks  = self._chunk_text(content, chunk_size=500)

            for i, chunk in enumerate(chunks):
                chunk_id = hashlib.md5(f"{document_id}_{i}_{chunk[:50]}".encode()).hexdigest()
                ids.append(chunk_id)
                docs.append(chunk)
                metas.append({
                    "document_id": document_id,
                    "title": title,
                    "version": version,
                    "effective_date": effective_date,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                })

        if ids:
            # ChromaDB 会自动生成 Embedding
            self._collection.upsert(ids=ids, documents=docs, metadatas=metas)
            logger.info(f"知识库导入 {len(ids)} 个文档片段")

        return len(ids)

    async def add_documents_async(self, documents: List[Dict[str, str]]) -> int:
        """异步导入文档；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.add_documents, documents)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        语义检索：根据 query 返回最相关的文档片段。

        ChromaDB 内部自动将 query 转为向量，与存储的文档向量做余弦相似度匹配。
        """
        results = self._collection.query(
            query_texts=[query],
            n_results=top_k,
        )

        items = []
        if results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                items.append({
                    "document_id": meta.get("document_id", ""),
                    "title":    meta.get("title", ""),
                    "content":  doc,
                    "score":    round(1.0 - dist, 4),  # ChromaDB 返回距离，转为相似度
                    "chunk":    meta.get("chunk_index", 0),
                    "version":  meta.get("version", ""),
                    "effective_date": meta.get("effective_date", ""),
                })

        return items

    async def search_async(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """异步检索；ChromaDB 客户端为同步实现，因此放入线程池执行。"""
        return await asyncio.to_thread(self.search, query, top_k)

    @property
    def doc_count(self) -> int:
        return self._collection.count()

    async def doc_count_async(self) -> int:
        """异步获取文档片段数量。"""
        return await asyncio.to_thread(self._collection.count)

    # ── MCP 工具 handler ─────────────────────────────────────────────────────

    async def search_handler(self, params: Dict[str, Any], context: Any) -> List[Dict]:
        """
        作为 MCP 工具的 handler 注册。

        MCPToolManager.register(Tool(
            name="knowledge_search",
            handler=kb.search_handler,
            ...
        ))
        """
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        return await self.search_async(query, top_k=top_k)

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _chunk_text(self, text: str, chunk_size: int = 500) -> List[str]:
        """将长文本按 chunk_size 切片，保留语义完整性（按句号/换行切分）。"""
        if len(text) <= chunk_size:
            return [text] if text.strip() else []

        chunks = []
        current = ""
        # 按句子切分
        sentences = text.replace("\n", "。").split("。")
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > chunk_size:
                if current:
                    chunks.append(current)
                current = sent
            else:
                current = f"{current}。{sent}" if current else sent

        if current:
            chunks.append(current)

        return chunks

    def _load_default_docs(self) -> None:
        """导入默认知识库文档（客服场景常见问题）。"""
        default_docs = [
            {
                "document_id": "policy-attendance-v1",
                "version": "1.0",
                "effective_date": "2026-01-01",
                "title": "三班制考勤与交接规范",
                "content": (
                    "A班 08:00-16:00，B班 16:00-24:00，C班 00:00-08:00。"
                    "员工应在班次开始前十五分钟完成到岗打卡。漏卡需在两个工作日内提交补卡申请，"
                    "由直属主管确认后交人事复核。跨日夜班以班次开始日期归属考勤日。"
                ),
            },
            {
                "document_id": "policy-leave-overtime-v1",
                "version": "1.0",
                "effective_date": "2026-01-01",
                "title": "请假与加班申请制度",
                "content": (
                    "普通请假应提前一个工作日提交；紧急情况应先通知班组长，并在返岗后补齐材料。"
                    "加班须先获得直属主管批准，系统记录与实际打卡同时作为核算依据。"
                    "涉及个人明细的查询必须完成员工身份校验。"
                ),
            },
            {
                "document_id": "equipment-repair-v1",
                "version": "1.0",
                "effective_date": "2026-01-01",
                "title": "设备故障分级与报修流程",
                "content": (
                    "设备故障分为P1停线、P2降速、P3一般异常。发现人应先确保人员安全，"
                    "记录设备编号、区域、报警码和故障现象。P1故障立即通知值班工程师与班组长，"
                    "未经授权不得复位安全联锁。报修单提交后由维修调度分派。"
                ),
            },
            {
                "document_id": "process-governance-v1",
                "version": "1.0",
                "effective_date": "2026-01-01",
                "title": "工艺文件使用规范",
                "content": (
                    "现场作业必须使用知识库中标记为生效状态的最新版本工艺文件。"
                    "回答工艺参数时应同时给出文件编号、版本和生效日期；当资料缺失或版本冲突时，"
                    "停止给出确定参数并升级给工艺工程师确认。"
                ),
            },
            {
                "document_id": "workflow-approval-v1",
                "version": "1.0",
                "effective_date": "2026-01-01",
                "title": "企业流程审批规则",
                "content": (
                    "系统支持查询请假、领料、设备停机与工艺变更流程。查询需提供申请单号。"
                    "智能客服可以生成申请草稿和解释所需材料，但提交、撤回、修改审批数据前必须由员工确认。"
                    "审批结果以业务系统返回状态为准。"
                ),
            },
            {
                "document_id": "security-handoff-v1",
                "version": "1.0",
                "effective_date": "2026-01-01",
                "title": "人工协同与信息安全",
                "content": (
                    "低置信度、多部门争议、涉及人身安全、停线、薪资争议或权限变更的请求应升级人工。"
                    "不得索取密码、完整身份证号、银行卡号等敏感信息。日志只记录必要业务字段，"
                    "人工任务按用户与会话隔离并保留审计轨迹。"
                ),
            },
        ]
        self.add_documents(default_docs)
        logger.info(f"已导入默认知识库: {len(default_docs)} 篇文档")
