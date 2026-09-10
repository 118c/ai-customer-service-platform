# 架构设计

## 分层结构

```text
API / Auth / Correlation ID
        │
LangGraph 服务工作流 ── Redis Checkpoint
        │
意图共识 ── 动态 Agent 路由 ── HITL
   │              │                 │
模型网关        RAG 工具链       业务连接器
   │              │                 │
主备模型      ChromaDB/Redis    EAM·MES·OA·HR
```

### 接入层

FastAPI 提供统一 REST API，负责请求校验、访问密钥校验、关联 ID 和耗时响应头。访问密钥未配置时保持开发环境易用；生产环境应由 API Gateway 完成 SSO、租户与权限注入。

### 编排层

`EmployeeServiceWorkflow` 将一次服务请求建模为 LangGraph 状态机：读取上下文、识别意图、补充知识和业务数据、执行 Agent、规划业务操作、人工复核、执行业务操作、持久化结果。每个请求使用独立 `request_id` 作为检查点线程，避免同一会话的多个并发请求互相覆盖。

人工复核节点使用持久化中断。中断前只生成待执行动作，不产生外部写入；恢复后根据 `approve`、`edit` 或 `reject` 决定是否调用业务连接器。

### 智能层

意图识别融合三种独立信号：

```text
intent_score = llm_score × 0.70 + semantic_score × 0.20 + keyword_score × 0.10
```

Agent 路由结合领域匹配、历史成功率、延迟和监控惩罚。专业 Agent 不可用时回退综合服务 Agent；主备模型均不可用时转入本地连续性通道。

### 数据与集成层

- Redis：短期会话、24 小时 TTL、LangGraph 检查点。
- ChromaDB：制度、SOP 与流程知识向量索引。
- SQLite 本地参考服务：提供稳定的业务接口实现和审计记录。
- HTTP 企业连接器：对接 HR、EAM/MES、OA/BPM，读写契约与本地服务一致。

## 可靠性边界

- 模型通道按连续失败次数熔断，并在恢复窗口后重新探测。
- RAG 工具配置超时、缓存、熔断和回退结果。
- 写操作使用幂等键并在工作流中保证复核先于执行。
- Redis 不可用时进程内检查点可维持单实例服务；该模式不支持跨实例恢复。
- Prometheus 暴露请求量、延迟、Agent 成功率和错误指标。

## 生产扩展

企业落地时建议在现有边界外补充：API Gateway 与 OIDC、租户级 RBAC、集中日志与 Trace、消息队列异步任务、数据库高可用、知识文档审批流和敏感字段脱敏策略。
