# 本地产品体验

本指南用于在无企业内网依赖的环境中体验完整员工服务链路。服务使用本地连续性模型、内置知识资料和参考业务实现；接口字段与企业 HTTP 连接器保持一致。

## 启动服务

后端：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

前端：

在项目的 `web` 目录执行：

```bash
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。

## 建议体验路径

1. 输入“跨日夜班的考勤日期怎么计算？”，打开回答下方的“查看参考资料”。
2. 继续输入“帮我提交领料申请，物料编码 MAT-1008，数量 20”。
3. 核对确认卡，点击“确认提交”，查看业务编号回执。
4. 输入“设备 EQ-A17 出现 E104 故障，请帮我提交报修”，体验设备报修确认流程。

员工页面不会展示意图标签、服务路由、模型通道或检索分数。需要说明系统内部过程时，可使用上一条响应的 `request_id` 在本机查询：

```bash
curl http://127.0.0.1:8000/admin/traces/REQUEST_ID
```

该追踪包含三路识别得分、路由选择、知识检索状态、模型通道与业务执行信息。生产环境配置 `ADMIN_API_KEY` 后，调用方需通过 `X-Admin-Key` 或 Bearer Token 提交管理员凭证。

## 切换企业服务

配置以下环境变量后重启后端：

```dotenv
ENTERPRISE_API_BASE_URL=https://enterprise-api.example.com
ENTERPRISE_API_TOKEN=replace-with-secret
```

业务查询与写入会切换到 `/v1/queries`、`/v1/actions` 和 `/v1/tasks/query`。对接字段、幂等规则与错误约定见 [企业 API 契约](api-contracts.md)。
