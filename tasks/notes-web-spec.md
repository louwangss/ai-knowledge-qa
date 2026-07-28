# Spec：独立笔记 Web 前端（第一阶段）

> 历史规格：记录 React 迁移第一阶段的约束。Phase 7 已完成全部工作区迁移并退役原型前端，当前状态以 `README.md` 与 `tasks/plan.md` 为准。

## 目标

为单用户本地演示项目增加独立的 React + TypeScript 笔记页，消除 Gradio 队列和组件整块重绘带来的交互等待。新页面先覆盖笔记列表、读取、新建、自动保存、冲突处理和删除；现有 Gradio 问答、文档和笔记页继续保留，用户数据仍以 MySQL 为权威来源。

## 已确认假设

- 第一阶段只迁移笔记，不重写问答、文档上传和多 Agent 工作流。
- 新页面与 FastAPI 使用同源 API；开发环境由 Vite 代理，构建产物可由 FastAPI 托管。
- `APP_ACCESS_TOKEN` 不进入前端构建、浏览器存储或 URL。统一启动器生成单次启动凭证，前端用它换取进程内 Web 会话和 HttpOnly Cookie；仍保留 Bearer Token 兼容 Gradio。
- 笔记继续使用纯文本标题和正文，不在本阶段引入富文本、Markdown 块编辑或离线同步。
- 600 ms 自动保存防抖是可调整的本地交互启发值，不是容量或安全边界；通过真实浏览器观测保存请求数量、失败率和主观输入延迟再校准。

## 技术栈与命令

- React、TypeScript、Vite、Vitest、Testing Library。
- 开发：`cd web && npm run dev`
- 前端测试：`cd web && npm test -- --run`
- 类型与构建：`cd web && npm run build`
- 后端针对性测试：`pytest tests/test_auth.py tests/test_notes.py tests/test_launcher.py -q`
- 一键启动：`start.bat`

## 目录

```text
web/
  src/             React 页面、API 客户端、状态与样式
  package.json     前端依赖和质量命令
app/               Cookie 会话、笔记版本契约、静态托管
tests/             后端认证、笔记冲突和启动器测试
tasks/notes-web-spec.md
```

## 接口契约

- 现有 `/api/v1/notes` CRUD 路径不变。
- 笔记响应新增只读 `version`，摘要新增 `updated_at`，均为向后兼容字段。
- 更新请求可选携带 `version`；版本不匹配返回 HTTP 409，旧 Gradio 不携带版本时保持原行为。
- `POST /api/v1/web/session` 仅接受本机请求，用短期启动凭证或长期 access token 换取 HttpOnly、SameSite=Strict Cookie；服务重启后会话失效。
- Cookie 写请求必须来自明确允许的本机 Origin；Bearer 调用保持原契约。

## 交互与视觉

- 笔记切换先更新本地选中态，缓存命中时不显示加载动画；未命中只在编辑区显示轻量占位。
- 新建和删除使用乐观更新，后台失败时恢复并显示可重试错误。
- 输入立即显示“未保存”，防抖后后台保存；保存冲突不覆盖本地正文，提供加载服务器版本或用本地版本覆盖。
- 桌面采用紧凑侧栏 + 无边框编辑器；移动端侧栏可收起。所有按钮可键盘操作，状态使用文本而非只依赖颜色。

## 测试策略

- 后端单元/集成测试覆盖：Cookie 认证、Origin 限制、凭证不泄露、版本冲突、旧调用兼容。
- 前端组件测试覆盖：初始加载、即时切换、自动保存状态、冲突提示、乐观删除回滚。
- 构建后检查静态资源中不存在真实 `APP_ACCESS_TOKEN`；浏览器检查控制台、网络状态、键盘操作和 320/768/1440 px 布局。

## 边界

- 始终：保留 MySQL 原始笔记；输入在 API 边界校验；提交前运行测试、构建和敏感模式检查。
- 先确认：改成公网认证、增加多用户、修改数据库列、开启跨域来源或引入富文本渲染。
- 绝不：把 token 写入 `localStorage`、前端环境变量、Git 或日志；删除 Gradio；不可逆改写现有笔记。

## 验收标准

- 已缓存笔记切换为同步本地状态更新，不经过 Gradio queue；创建/删除点击后立即反馈。
- 连续输入只在停顿后保存，串行合并同一笔记的请求；失败、409 和离线状态可见且不丢本地文本。
- `start.bat` 同时管理 FastAPI、Gradio 和新笔记页，并自动打开新页面。
- Bearer 客户端和既有测试保持兼容；前后端测试、构建、`git diff --check` 通过。

## 非目标

- 完整账号登录、JWT、多租户、云端部署、PWA 离线编辑、多人实时协作、富文本编辑器。
