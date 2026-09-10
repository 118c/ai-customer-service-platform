# 企业员工服务业务 Skills

平台启动时从 `SERVICE_SKILLS_DIR` 加载业务规则，并按关键词和 Agent 类型注入系统提示词。业务规范可以独立更新，通过接口热加载，无需修改 Agent 源码。

```text
skills/employee_service/SKILL.md   # 综合接待、澄清与人工协同
skills/equipment_service/SKILL.md  # 设备排障、停机边界与报修
skills/policy_service/SKILL.md     # 考勤、薪资与制度解释
skills/workflow_service/SKILL.md   # 流程查询、申请与审批边界
```

## 文件约定

```yaml
---
name: 设备服务处理规范
description: 适用于设备故障诊断与报修受理
keywords: 设备,机台,故障,报修,停机,错误码
agents: technical
enabled: true
---
```

- `keywords`：命中任一关键词后参与匹配。
- `agents`：支持 `general`、`technical`、`policy`、`workflow`、`escalation`。
- `enabled`：设置为 `false` 时不加载。
- 规则正文应明确角色边界、处理流程、升级条件和禁止事项。

## 热加载

```bash
curl -X POST http://localhost:8000/skills/reload
curl http://localhost:8000/skills
```
