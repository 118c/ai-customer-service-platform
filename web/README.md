# 企业员工智能客服 Web

基于 Vue 3 的员工端自然语言服务界面。页面只呈现员工需要使用的信息：对话、资料依据、业务确认与办理回执；内部意图判断、Agent 路由、模型状态和检索分数由后端运维接口承载。

## 本地运行

```bash
npm install
npm run dev
```

访问 `http://localhost:5173`。开发服务器默认将 `/api` 转发到 `http://localhost:8000`，也可通过 `VITE_API_URL` 指向其他服务地址。员工身份优先读取网关注入的 `window.__EMPLOYEE_CONTEXT__.employeeId`，其次读取 `VITE_EMPLOYEE_ID`；本地未配置时使用参考工号 `E1001`。

## 生产构建

在项目根目录执行：

```bash
docker compose up -d --build
```

Nginx 提供静态资源并转发 `/api`。身份信息与访问凭证应由企业 SSO 或网关注入，不在浏览器界面中配置。
