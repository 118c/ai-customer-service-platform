# 企业员工智能客服 Web

基于 Vue 3 的双入口 Web 应用。员工端只呈现对话、资料依据、业务确认与办理回执；独立质量控制台用于授权人员查看完整工作流评测、趋势和运行健康。内部意图判断、路由详情、模型状态和检索分数不会进入员工界面。

## 本地运行

```bash
npm install
npm run dev
```

访问 `http://localhost:5173` 使用员工服务，访问 `http://localhost:5173/operations.html` 使用质量控制台。开发服务器默认将 `/api` 转发到 `http://localhost:8000`，也可通过 `VITE_API_URL` 指向其他服务地址。员工身份优先读取网关注入的 `window.__EMPLOYEE_CONTEXT__.employeeId`，其次读取 `VITE_EMPLOYEE_ID`；本地未配置时使用参考工号 `E1001`。

## 生产构建

在项目根目录执行：

```bash
docker compose up -d --build
```

Nginx 提供静态资源并转发 `/api`。独立部署时质量控制台可使用 `ADMIN_API_KEY`；企业环境建议由 SSO、网关和 RBAC 统一控制员工端与运维端权限。
