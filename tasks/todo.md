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
- [ ] Task 2：消除并行检索共享 SQLAlchemy Session。
  - [ ] 每个工作线程独立创建并关闭 DB Session。
  - [ ] 验证 normal/deep 返回结构不变。
  - [ ] 运行 `pytest tests/test_memory.py tests/test_agents.py -q`。
- [ ] Task 3：完整删除会话摘要和 Redis 状态。
  - [ ] 覆盖存在 `SessionSummary` 的删除测试。
  - [ ] 覆盖跨用户访问/删除测试。
  - [ ] 运行 `pytest tests/test_api.py -q -k "session"`。
- [ ] Checkpoint A：运行 `pytest -q`、`git diff --check` 和三条手动故障路径。

## Phase 2：安全与资源边界

- [ ] Task 4：流式限量上传和可恢复的文档删除。
  - [ ] 增加 `MAX_UPLOAD_BYTES` 配置及 413 测试。
  - [ ] 保证重复、失败和超限上传无临时残留。
  - [ ] Chroma 删除失败时保留 MySQL/源文件并返回可重试错误。
  - [ ] 运行 `pytest tests/test_documents.py tests/test_api.py -q`。
- [ ] Task 5：增加单用户 Bearer token 与资源归属校验。
  - [ ] 无/错 token 返回 401。
  - [ ] 其他 `user_id` 无法访问资源。
  - [ ] 日志和响应不泄露 token。
  - [ ] 运行 `pytest tests/test_auth.py tests/test_api.py -q`。
- [ ] Task 6：前端统一携带 token，服务默认绑定 `127.0.0.1`。
  - [ ] 普通请求与 SSE 请求均携带认证头。
  - [ ] 手动验证上传、两种问答、切换和删除会话。
  - [ ] 运行 `pytest tests/test_frontend_client.py tests/test_auth.py -q`。
- [ ] Checkpoint B：全量测试、`pip check`、认证和资源失败路径验证。

## Phase 3：简历项目证据

- [ ] Task 7：增加不记录敏感正文的请求/turn 可观测性。
- [ ] Task 8：增加可公开、可复现的 RAG 离线评测。
- [ ] Task 9：增加无密钥 CI，完善 README 架构、取舍和实测证据。
- [ ] Checkpoint C：全新环境验证、演示验证、安全复审和简历表述复审。

## 每个 Task 的统一完成条件

- [ ] 新行为有测试，修复前可复现、修复后通过。
- [ ] 相关测试与全量回归通过。
- [ ] `git diff --check` 通过，暂存区只有本 Task 文件。
- [ ] 没有提交 `.env`、`data/`、个人文档、缓存或真实凭据。
- [ ] 独立中文 Git 提交；不自动 push。
