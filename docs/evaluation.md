# 评测方法

## 目标

评测分为两层：确定性回归用于阻止分类与编排逻辑退化；目标模型评测用于衡量真实语言理解、答案忠实度和业务可用性。两类结果不得混为同一质量结论。

## 意图回归

`evaluation/datasets/factory_intents.jsonl` 包含 55 条工厂员工服务表达，覆盖工艺规范、考勤、薪资、设备故障、报修、流程查询、申请提交、人工协同、问候、反馈和其他请求。

```bash
pytest tests/test_llm_gateway.py
```

CI 门槛为准确率不低于 92%。新增业务意图时，应先补充不少于 5 条表达并更新混淆矩阵。

## 端到端评测

完整工作流数据位于 `evaluation/datasets/workflow_cases.jsonl`，通过受保护接口运行并集中存储：

```bash
curl -X POST http://localhost:8000/admin/evaluations/run \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"dataset_version":"2026.09"}'
```

端到端报告关注：

- Agent 路由是否与意图一致；
- 答案是否引用检索上下文且无明显事实冲突；
- 写操作是否先暂停、后复核、再执行；
- 超时和服务故障是否触发受控降级；
- P50、P95 延迟和失败率是否满足发布阈值。

固定工作流集覆盖普通咨询、知识问答、业务查询、报修、申请、取消、修改和人工协同。每轮保存 `request_id` 并关联内部 Trace；报告还会检查多轮记忆是否落盘，以及员工响应中是否混入意图、路由、模型和降级明细等内部字段。确定性全链路发布门槛为 95%。

## RAG 专项评测

知识文档使用稳定 `document_id`、版本号和生效时间。独立数据集位于 `evaluation/datasets/rag_cases.jsonl`：

```bash
curl -X POST http://localhost:8000/admin/evaluations/rag/run \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"dataset_version":"2026.09"}'
```

检索层门槛为 Recall@3 ≥ 90%、MRR ≥ 0.80、nDCG@3 ≥ 0.85、Citation Precision ≥ 90%、Citation Recall ≥ 85%；答案层 Faithfulness 与 Answer Relevance 均不低于 85%。答案采用双层判定：确定性规则校验标准事实、禁止事实和引用关系，LLM Judge 只能降低最终分数，不能覆盖确定性失败。

报告写入 SQLite 评测仓库，并记录 Git Commit、数据集、模型、Prompt/Skill、知识库版本和环境类型。可通过 `GET /admin/evaluations`、`GET /admin/evaluations/{run_id}`、`GET /admin/evaluations/{run_id}/failures` 与 `GET /admin/evaluations/trends` 查询。

可靠性用例可使用 `evaluation/faults.py` 的受控依赖故障适配器覆盖检索超时、业务查询失败和业务写入失败。运行链路会记录 `degradation_events`，员工响应仅呈现可操作的降级提示。

业务操作报告额外记录 `business_action_success_rate`、`business_action_latency_ms`、`confirmation_success_rate`、`workflow_resume_success_rate`、`cancelled_write_count`、`duplicate_write_count` 与 `idempotency_hit_count`。预期故障用例的“受控降级通过”和真实业务动作成功率分开统计，避免以降级通过掩盖下游写入失败。

专业 Agent 连续两个采集周期达到隔离阈值后停止接收新请求，恢复窗口结束进入半开探测；成功后恢复服务，失败则再次隔离。该状态可从运维监控摘要与 Prometheus 指标查看。

## 报告口径

本地连续性通道只用于验证接口、路由和工作流可运行，不代表目标模型的语义质量。对外发布准确率、忠实度或响应时间前，必须固定模型版本、检索索引、数据集版本与硬件环境，并保存原始运行记录。
