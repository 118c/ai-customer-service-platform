# 企业 API 契约

设置 `ENTERPRISE_API_BASE_URL` 与 `ENTERPRISE_API_TOKEN` 后，平台通过 Bearer Token 调用以下接口。所有响应均为 JSON。

## 查询业务数据

`POST /v1/queries`

```json
{
  "type": "attendance.lookup",
  "payload": {
    "employee_id": "E1001"
  }
}
```

响应：

```json
{
  "found": true,
  "data": {
    "employee_id": "E1001",
    "work_date": "2026-09-10",
    "status": "normal"
  }
}
```

`type` 当前支持：

- `attendance.lookup`
- `repair.lookup`
- `workflow.lookup`

## 执行业务操作

`POST /v1/actions`

请求头包含 `Idempotency-Key`，下游系统必须对相同幂等键返回同一业务结果。`approved` 只会在工作流已通过人工复核后发送。

```json
{
  "action_type": "repair.create",
  "payload": {
    "requester_id": "E1001",
    "equipment_id": "EQ-A17",
    "description": "E104 故障"
  },
  "requires_review": true,
  "description": "创建设备报修单",
  "approved": true
}
```

响应：

```json
{
  "success": true,
  "record_id": "REP-20260910-001",
  "status": "submitted"
}
```

`action_type` 当前支持：

- `repair.create`
- `workflow.submit`
- `handoff.create`

## 查询近期任务

`POST /v1/tasks/query`

```json
{
  "limit": 20
}
```

响应：

```json
{
  "items": [
    {
      "id": "WF-202609-001",
      "type": "material_request",
      "status": "pending",
      "updated_at": "2026-09-10T08:30:00+08:00"
    }
  ]
}
```

## 错误约定

- `400`：字段或业务规则不满足。
- `401/403`：身份或权限校验失败。
- `404`：业务对象不存在。
- `409`：幂等冲突或状态冲突。
- `429`：下游限流。
- `5xx`：下游暂时不可用，平台将计入连接器失败并返回受控提示。
