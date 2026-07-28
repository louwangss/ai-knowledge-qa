# 修复执行清单

## 已完成基线

- [x] 扫描当前 Git 历史、stash 和不可达对象中的常见敏感模式。
- [x] 加强 `.gitignore`，保留 `.env.example` 与 `requirements.lock` 可提交。
- [x] 在 `chore/repository-hygiene` 分支提交仓库整理：`0733934`。

## Phase 1：数据一致性

- [x] Task 1：修复 Chat 冷恢复顺序和阶段化失败清理。
  - [x] 先增加重复消息、精确回滚、完成后辅助失败测试。
  - [x] 保持 SSE 事件契约不变。
  - [x] 运行 `pytest tests/test_chat_consistency.py tests/test_memory.py -q`。
- [x] Task 2：消除并行检索共享 SQLAlchemy Session。
  - [x] 每个工作线程独立创建并关闭 DB Session。
  - [x] 验证 normal/deep 返回结构不变。
  - [x] 运行 `pytest tests/test_memory.py tests/test_agents.py -q`。
- [x] Task 3：完整删除会话摘要和 Redis 状态。
  - [x] 覆盖存在 `SessionSummary` 的删除测试。
  - [x] 覆盖跨用户访问/删除测试。
  - [x] 运行会话、Chat 与记忆针对性测试。
- [x] Checkpoint A：完成自动化验证后，再做真实 MySQL/Redis 联调。
  - [x] 运行 `pytest -q` 与 `git diff --check`。
  - [x] 覆盖 Redis miss、LLM 中断和带摘要会话删除的自动化故障路径。
  - [x] 使用本地 MySQL/Redis 完成一次非破坏性联调。

## Phase 2：安全与资源边界

- [x] Task 4：流式限量上传和可恢复的文档删除。
  - [x] 增加 `MAX_UPLOAD_BYTES` 配置及 413 测试。
  - [x] 保证空文件、重复、失败和超限上传无临时残留。
  - [x] Chroma 删除失败时保留 MySQL/源文件并返回可重试错误。
  - [x] 运行 `pytest tests/test_documents.py tests/test_api.py -q`。
- [x] Task 5：增加单用户 Bearer token 与资源归属校验。
  - [x] 无/错 token 返回 401。
  - [x] 其他 `user_id` 无法访问资源。
  - [x] 日志、响应、SSE 和 OpenAPI 不泄露 token。
  - [x] 运行 `pytest tests/test_auth.py tests/test_api.py -q`。
- [ ] Task 6：前端统一携带 token，服务默认绑定 `127.0.0.1`（代码完成，待浏览器手动验收）。
  - [x] 普通请求与 SSE 请求均携带认证头。
  - [x] FastAPI 与 Gradio 默认绑定 `127.0.0.1`，外部监听要求 token。
  - [ ] 手动验证上传、两种问答、切换和删除会话。
  - [x] 使用隔离测试用户完成真实 API 上传、normal/deep、切换读取和删除联调。
  - [x] 运行 `pytest tests/test_frontend_client.py tests/test_auth.py -q`。
- [ ] Checkpoint B：全量测试、`pip check`、认证和资源失败路径验证（自动化部分完成，待浏览器验收）。
  - [x] 全量测试和 `pip check` 通过。
  - [x] 无 token、错误 token、正确 token 三态有自动化覆盖。
  - [x] 超限上传、Chroma 删除异常和 LLM 中断有自动化覆盖。
  - [x] `.env`、`data/` 和测试产物未进入暂存区。
  - [ ] 浏览器完成上传、normal/deep 问答、切换和删除会话。

## Phase 3：简历项目证据

- [x] Task 7：增加不记录敏感正文的请求/turn 可观测性。
  - [x] request/turn ID 串联请求、检索、首 token、完成与失败阶段。
  - [x] 日志仅记录 ID、模式、阶段、计数、耗时和错误类型。
  - [x] `pytest tests/test_observability.py tests/test_chat_consistency.py -q`。
- [x] Task 8：增加可公开、可复现的 RAG 离线评测。
  - [x] 公开合成数据集不含用户文档或私有数据。
  - [x] 无密钥 CLI 输出数据集哈希、检索器版本、逐题结果和三项指标。
  - [x] 保存 `top_k=2` 的实际基线结果并明确适用局限。
  - [x] `pytest tests/test_evaluation.py -q` 与全量回归通过。
- [x] Task 9：增加无密钥 CI，完善 README 架构、取舍和实测证据。
  - [x] CI 使用占位环境变量执行依赖检查、编译、全量测试和离线评测。
  - [x] 补齐代码实际使用的 LangChain 拆分包依赖声明。
  - [x] README 更新架构、恢复策略、安全边界、实测证据和简历表述。
  - [x] 文档明确小样本评测与 Gradio 外部监听的限制。
- [ ] Checkpoint C：全新环境验证、演示验证、安全复审和简历表述复审（自动化与真实 API 完成，待浏览器和托管 CI）。
  - [x] 99 项测试、`pip check`、源码编译和离线评测通过。
  - [x] 本地 MySQL/Redis/Chroma/DeepSeek 完成隔离式真实联调并清理测试数据。
  - [x] normal/deep、上传/删除、双会话列表/切换读取均验证成功。
  - [x] 完成日志脱敏、配置边界、Git 敏感模式和最终差异复审。
  - [ ] 使用浏览器交互验收 Gradio 全流程（当前未配置 Chrome DevTools MCP）。
  - [ ] push 后确认 GitHub Actions 首次运行成功。

## 每个 Task 的统一完成条件

- [ ] 新行为有测试，修复前可复现、修复后通过。
- [ ] 相关测试与全量回归通过。
- [ ] `git diff --check` 通过，暂存区只有本 Task 文件。
- [ ] 没有提交 `.env`、`data/`、个人文档、缓存或真实凭据。
- [ ] 独立中文 Git 提交；不自动 push。

## Phase 4：独立笔记 Web 前端

- [ ] Task 10：笔记版本与冲突接口。
  - [ ] 响应增加内容版本，带版本更新可原子拒绝过期写入。
  - [ ] 旧 Gradio 不传版本时保持兼容。
- [ ] Task 11：本机 Web 会话。
  - [ ] 启动凭证换取 HttpOnly Cookie，长期 token 不进入前端。
  - [ ] Cookie 写请求校验 Origin，Bearer 调用不受影响。
- [ ] Task 12：React 笔记页。
  - [ ] 列表、搜索、读取、新建、自动保存、冲突与删除可用。
  - [ ] 缓存切换和乐观操作不经过 Gradio queue。
  - [ ] 具备错误、空状态、响应式和键盘可访问性。
- [ ] Task 13：统一启动与验收。
  - [ ] `start.bat` 管理 FastAPI、Gradio、Vite 并打开笔记页。
  - [ ] 前端测试、构建、后端回归、浏览器验证和敏感模式检查通过。
