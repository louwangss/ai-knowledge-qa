# AI 知识库问答系统修复与作品集强化计划

> 归档说明：本文按阶段保留当时的实施决策，因此包含已失效的 Gradio 与旧 Redis 主链路描述。当前项目已由 React 完整替代 Gradio，MySQL 保存权威对话事实；现状以仓库源码与 `README.md` 为准。

## 1. 目标

在保留现有 RAG、多 Agent、三层记忆和 SSE 接口设计的前提下，先修复会造成数据错乱、越权访问或资源耗尽的问题，再补齐适合作为实习简历项目展示的验证、可观测性和工程化证据。

### 验收标准

- Redis 冷启动后发送消息不会重复，流式失败或客户端断开不会产生孤立消息。
- 异步并行检索不跨线程共享同一个 SQLAlchemy `Session`。
- 会话及其摘要、Redis 状态能完整删除；文档删除失败时不会留下不可恢复的半删除状态。
- 未携带正确凭证的 API 请求被拒绝，请求中的 `user_id` 不能绕过单用户隔离。
- 超限或非法上传在进入解析、向量化前失败，并且不残留临时文件、数据库记录或新向量。
- SSE 事件类型 `token/status/sources/done/error` 和 normal/deep 两种模式保持兼容。
- 全量测试、静态差异检查通过；README 能展示架构、测试方法、关键安全决策和可复现实验结果。

## 2. 非目标

- 本轮不实现注册、密码找回、角色权限、JWT 刷新或完整多租户 SaaS。
- 不重写 LangGraph 工作流，不更换 MySQL、Redis、ChromaDB 或 LLM 供应商。
- 不删除用户现有 `data/`、`.env`、stash 或个人文件，也不重写 Git 历史。
- 不在没有真实评测数据前宣称检索准确率、并发能力或生产可用性。

## 3. 关键设计决策

1. **先保证状态一致性，再加认证。** Chat 和删除链路当前存在确定性数据错误，先用回归测试固定行为，减少认证改造掩盖原始问题的风险。
2. **MySQL 保存权威业务记录，Redis 和 Chroma 视为可重建派生状态。** 派生状态操作失败时保留源记录和重试路径，不静默制造“数据库已删但向量仍可检索”的状态。
3. **单用户演示版采用 Bearer access token。** 使用 FastAPI `HTTPBearer` 依赖和恒定时间比较，后端固定 `APP_USER_ID`；这是简历演示所需的最小安全边界，不伪装成完整用户系统。
4. **保持现有 SSE 契约。** 修复只调整内部提交顺序和清理策略，避免同时重写前端事件消费。
5. **上传采用分块读取、增量哈希和临时文件。** `UploadFile` 的 spooled file 能降低大文件常驻内存，但不等于业务大小限制；必须在进入解析和 embedding 前实施硬上限。
6. **数值均可配置。** 首版 `MAX_UPLOAD_BYTES` 建议默认 25 MiB：当前本地最大样本文档约 20 MiB，预留约 25% 余量。该值是面向当前单机演示的启发式默认值，上线前须用真实 PDF 的解析耗时、峰值内存和 embedding 成本重新校准。

## 4. 关键状态流

```text
请求鉴权与归属校验
  -> 从 MySQL 恢复“当前请求之前”的 Redis 历史
  -> 持久化并标记当前 user 消息
  -> Redis 追加同一消息标识
  -> 独立 DB Session + 并行 Chroma 检索
  -> SSE 流式生成
  -> 持久化 assistant 消息
  -> 更新 Redis / 摘要 / 事件 / last_active
  -> sources + done

生成未完成失败：按消息标识清理当前 pending turn
回答已持久化后辅助步骤失败：保留完整问答，仅记录并补偿派生状态
```

## 5. 分阶段任务

### Phase 1：P0 数据一致性

#### Task 1：修复 Chat 恢复顺序与失败清理

**说明：** 将 Redis 恢复限定为当前请求之前的持久历史，为消息增加可精确定位的标识，并按“未完成/已完成”阶段决定是否回滚，替代无条件 `RPOP`。

**验收条件：**

- Redis 为空但 MySQL 已有历史时，新 user 消息只出现一次。
- 生成前失败只删除当前 pending user 消息，不会误删其他并发或既有消息。
- assistant 已持久化后，情景记忆或 `last_active` 更新失败不会删除已完成回答。

**验证：** `pytest tests/test_chat_consistency.py tests/test_memory.py -q`

**依赖：** 无

**预计文件：** `app/api/routes_chat.py`、`memory/short_term.py`、`tests/test_chat_consistency.py`

**规模：** M

#### Task 2：隔离并行检索使用的数据库 Session

**说明：** Chroma 查询继续并行；需要访问 MySQL 的记忆读取在工作线程内创建、使用并关闭自己的 `SessionLocal`，不再把请求 Session 同时交给多个线程。

**验收条件：**

- 同一个 SQLAlchemy `Session` 不会被两个 `to_thread` 任务并发使用。
- normal/deep 两种模式返回的数据结构保持不变。
- 工作线程异常时其数据库 Session 仍会关闭。

**验证：** `pytest tests/test_memory.py tests/test_agents.py -q`

**依赖：** Task 1

**预计文件：** `memory/retrieve.py`、`db/database.py`、`tests/test_memory.py`

**规模：** M

#### Task 3：修复会话删除的完整性

**说明：** 删除会话时显式处理 `SessionSummary` 和对应 Redis keys，并保证 session 必须属于请求用户。

**验收条件：**

- 存在 `session_summary` 时删除会话不会触发外键错误。
- 删除成功后 chat、episodic、summary 和 Redis 会话状态均不存在。
- 其他用户不能读取或删除该会话。

**验证：** `pytest tests/test_api.py -q -k "session"`

**依赖：** Task 1

**预计文件：** `app/api/routes_sessions.py`、`memory/short_term.py`、`tests/test_api.py`

**规模：** M

### Checkpoint A：一致性基线

- 运行 `pytest -q`。
- 运行 `git diff --check`。
- 手动模拟 Redis miss、LLM 中断和带摘要会话删除。
- 每个 Task 形成独立中文 Git 提交；Checkpoint 通过后再进入安全边界改造。

### Phase 2：P0/P1 安全与资源边界

#### Task 4：限制上传并保证文档生命周期可恢复

**说明：** 分块读取 `UploadFile`，增量计算 SHA-256，先写同目录临时文件，校验通过后原子改名；解析、向量化或删除失败时保留明确的恢复路径。

**验收条件：**

- 超过 `MAX_UPLOAD_BYTES` 的上传返回 413，且不进入解析/embedding。
- 空文件、重复文件和处理失败不残留临时文件；返回信息不暴露本机路径或内部异常。
- Chroma 删除失败时不删除 MySQL 权威记录和源文件，并返回可重试错误。

**验证：** `pytest tests/test_documents.py tests/test_api.py -q`

**依赖：** Checkpoint A

**预计文件：** `config.py`、`.env.example`、`app/api/routes_documents.py`、`rag/vector_store.py`、`tests/test_documents.py`

**规模：** M

#### Task 5：建立单用户 API 安全边界

**说明：** 引入 `APP_ACCESS_TOKEN`、`APP_USER_ID` 和统一认证依赖；根健康检查可公开，其余业务路由要求 Bearer token，并校验资源归属。

**验收条件：**

- 无凭证或错误凭证返回 401，正确凭证可访问。
- 请求参数中的其他 `user_id` 不能读取、修改或删除当前用户以外的数据。
- token 不出现在日志、SSE、异常响应和 OpenAPI 示例值中。

**验证：** `pytest tests/test_auth.py tests/test_api.py -q`

**依赖：** Task 3

**预计文件：** `config.py`、`.env.example`、`app/deps.py`、`app/main.py`、`tests/test_auth.py`

**规模：** M

#### Task 6：让前端适配认证并默认仅本机监听

**说明：** 统一封装 httpx 请求头，前端从环境变量读取 token；FastAPI/Gradio 默认绑定 `127.0.0.1`，显式配置后才允许局域网监听。

**验收条件：**

- 前端所有 API 请求自动携带认证头，SSE 请求同样生效。
- token 不进入浏览器聊天内容或可见状态文本。
- 默认启动不会监听所有网卡；配置为外部监听时必须同时存在 access token。

**验证：** `pytest tests/test_frontend_client.py tests/test_auth.py -q`，然后本机手动完成上传、普通问答、deep 问答、切换与删除会话。

**依赖：** Task 5

**预计文件：** `frontend/app.py`、`config.py`、`.env.example`、`tests/test_frontend_client.py`

**规模：** M

### Checkpoint B：可安全演示

- 运行 `pytest -q` 和 `pip check`。
- 使用无 token、错误 token、正确 token 各验证一次 API。
- 验证超限上传、Chroma 删除异常、客户端中断三条失败路径。
- 确认 `.env`、`data/`、测试产物未进入暂存区。

### Phase 3：P1/P2 简历项目证据

#### Task 7：增加可观测性但不记录敏感内容

**说明：** 为请求和 Chat turn 增加关联 ID，记录检索、首 token、总耗时和失败阶段；日志只保留 ID、计数和耗时，不记录完整问题、文档或 token。

**验收条件：**

- 一次 Chat 请求可通过 request/turn ID 串联主要阶段。
- 日志能区分认证、检索、LLM、持久化和派生状态失败。
- 自动测试证明日志不包含 access token 和完整用户输入。

**验证：** `pytest tests/test_observability.py -q`

**依赖：** Checkpoint B

**预计文件：** `app/main.py`、`app/api/routes_chat.py`、`app/observability.py`、`tests/test_observability.py`

**规模：** M

#### Task 8：建立可复现的 RAG 离线评测

**说明：** 使用可公开的小型样本文档和问题集，评估检索 Recall@K、来源命中和回答引用完整性；默认测试不调用付费 LLM。

**验收条件：**

- 一条命令可生成结构化评测结果。
- 数据集、指标定义、模型版本和运行日期可追溯。
- README 只展示实际运行得到的指标，并说明样本规模和局限。

**验证：** `pytest tests/test_evaluation.py -q`，再运行评测 CLI 并检查输出 schema。

**依赖：** Checkpoint B

**预计文件：** `evaluation/dataset.json`、`evaluation/run_eval.py`、`tests/test_evaluation.py`、`README.md`

**规模：** M

#### Task 9：补齐 CI、启动文档和演示材料

**说明：** 增加不依赖真实密钥的 CI 质量门禁，更新 README 的架构、威胁边界、故障恢复、测试与演示步骤；部署配置另开任务，不与修复混在一个提交。

**验收条件：**

- CI 在无真实 API key 情况下运行单元测试和依赖检查。
- README 能让面试官在 5 分钟内理解问题、架构、关键取舍和验证证据。
- 文档不包含个人绝对路径、真实密钥、私有数据截图或夸大指标。

**验证：** 本地执行与 CI 相同命令，检查 Markdown 链接与全仓敏感模式扫描。

**依赖：** Tasks 7、8

**预计文件：** `.github/workflows/test.yml`、`README.md`、`.env.example`、必要的测试配置文件

**规模：** M

### Checkpoint C：作品集完成

- 所有测试和 CI 通过。
- 从全新环境按 README 可以启动或明确看到缺少的外部服务。
- 完成一次 normal/deep 演示并保存非敏感的实际结果。
- 对最终差异执行安全、正确性、可维护性和简历表述复审。

## 6. 风险与缓解

| 风险 | 影响 | 缓解方式 |
|---|---|---|
| 修改 Chat 提交顺序引入新竞态 | 高 | 先写冷恢复、并发失败和断连回归测试；状态变更使用精确消息标识 |
| 认证改造破坏前端或脚本 | 高 | 保留接口形状，先落后端依赖测试，再统一改前端客户端 |
| Chroma 与 MySQL 跨存储无法原子提交 | 高 | MySQL/源文件保留为权威数据；失败可重试，后续增加补偿扫描 |
| 上传阈值不适合真实文档 | 中 | 环境变量可调；记录解析时间、峰值内存和 embedding 成本后校准 |
| 测试依赖真实 MySQL/Redis/LLM | 中 | 单元测试使用 dependency override/fake；另保留显式集成测试入口 |
| 简历指标失真 | 中 | 只报告固定数据集的实测结果，附样本量、日期、模型与局限 |

## 7. 公开资料依据

核对日期：2026-07-27。

- FastAPI 官方说明 `UploadFile` 使用 `SpooledTemporaryFile`，适合避免把大文件完整常驻内存，但仍需应用自行定义业务限制：<https://fastapi.tiangolo.com/tutorial/request-files/>
- Starlette 官方说明 multipart 解析提供 `max_files`、`max_fields`、`max_part_size` 等限制用于降低拒绝服务风险；`UploadFile.size` 由请求内容计算，比信任 `Content-Length` 更可靠：<https://www.starlette.io/requests/>
- FastAPI 官方提供 `HTTPBearer` 等可集成 OpenAPI 的安全依赖：<https://fastapi.tiangolo.com/reference/security/>

## 8. 执行规则

- 用户批准本计划后，从 Task 1 开始；每次只实施一个 Task 或一个明确 Checkpoint。
- 每个 Task 遵循“先失败测试、再最小修复、再全量回归、再独立提交”。
- 如果认证模型、SSE 契约、数据库 schema 或存储权威关系需要实质改变，暂停并只提交计划差异供用户重新确认。
- 不自动 push、发布、删除数据或清理 Git 历史。

## 9. Phase 4：独立笔记 Web 前端

详细契约见 `tasks/notes-web-spec.md`。按以下依赖顺序实施：

1. 扩展笔记响应版本和条件更新契约，先固定 409 冲突与旧客户端兼容行为。
2. 增加仅本机 Web 会话 Cookie，保留 Bearer Token，并用 Origin 校验保护 Cookie 写请求。
3. 创建 React + TypeScript 笔记前端，实现缓存切换、乐观新建/删除、串行防抖自动保存和冲突恢复。
4. 将 Vite 开发服务加入统一启动器，并让 FastAPI 可托管构建产物。
5. 运行前后端测试、构建与真实浏览器验收；本阶段形成独立 Git 提交，不删除 Gradio。

## 10. Phase 5：独立问答与会话 Web 前端

### 目标

在不改变现有 Chat SSE 契约、RAG、多 Agent 工作流和数据权威关系的前提下，把普通问答、深度研究与会话管理迁移到现有 React + TypeScript Web 工作区。React 直接消费 FastAPI SSE，Gradio 保留为回退入口。

### 非目标

- 本阶段不迁移文档上传与管理，不删除 Gradio，也不增加完整账号、多租户或公网认证。
- 不修改 `token/status/sources/done/error` 事件类型，不重写 LangGraph 或消息持久化流程。
- 不引入全局状态库、WebSocket、富文本渲染或离线问答。

### 状态流与关键取舍

```text
Web HttpOnly 会话
  -> 获取会话列表与最近会话历史
  -> 本地缓存会话消息并即时切换
  -> POST /api/v1/chat，逐块解析 SSE
  -> token 增量更新回答
  -> status 展示 deep 阶段
  -> sources 展示引用
  -> done 固化当前消息并刷新会话摘要
  -> error/断线保留已收到内容并提供明确重试提示
```

- 每个会话最多只有一个前端流任务；切换会话不取消后台生成，但重复发送会被禁用。
- 页面卸载时用 `AbortController` 结束浏览器连接；后端继续按现有断连清理策略保证一致性。
- 会话切换优先使用内存缓存；首次读取仅在消息区显示轻量加载状态，不阻塞侧栏。
- API 客户端统一使用 `credentials: "include"`，长期 token 不进入浏览器状态或存储。

### 实施顺序

1. **Task 14：固定 React Chat 客户端契约。** 增加会话、消息、来源和 SSE 事件类型；用失败测试覆盖分块事件、多个事件同块、错误事件与流中止。
2. **Task 15：实现会话与流式问答状态。** 加载/缓存/新建/切换/删除会话，串行消费普通与 deep 流，处理空输入、重复发送、错误和取消。
3. **Task 16：实现生产质量问答界面。** 在现有 Web 工作区加入问答/笔记导航、会话侧栏、消息流、模式切换、来源、阶段状态、输入框及响应式/键盘交互。
4. **Task 17：集成与验收。** 运行前端测试、TypeScript/构建、Python 回归和真实浏览器 normal/deep、会话切换/删除验收；检查控制台、网络、响应式和敏感凭证。

### 风险与缓解

| 风险 | 影响 | 缓解方式 |
|---|---|---|
| SSE 分块边界导致丢事件或 JSON 解析失败 | 高 | 独立增量解析器并覆盖任意分块测试 |
| 切换/删除会话与正在生成的请求竞态 | 高 | 每会话记录流状态；生成中禁止删除；响应按 session id 定向更新 |
| deep 状态与 token 顺序不同 | 中 | 事件按到达顺序归并，状态与正文分别存储 |
| React 与 Gradio 行为不一致 | 中 | 复用现有 API 和 SSE 契约，保留 Gradio 回退并做同一真实后端验收 |

### 验收标准

- 现有历史会话可读取，新建、切换和删除均有即时反馈；缓存命中切换不出现整页加载动画。
- normal/deep 都能逐 token 显示，deep 阶段状态和来源可见，错误与中断不会伪装成完成。
- 输入、发送、模式选择、会话操作可键盘使用；320/768/1440 px 布局可用。
- 浏览器控制台无错误或警告，业务请求使用 HttpOnly 会话，前端资源与存储中不存在真实 access token。
- 前端测试、TypeScript 检查、构建、Python 全量回归和 `git diff --check` 通过；形成独立中文 Git 提交，不自动 push。

## 11. Phase 6：React 文档管理

### 目标与边界

在现有 React 工作区迁移文档列表、上传和删除，直接复用 FastAPI `/api/v1/documents`；不修改解析、分块、Embedding、数据库结构和 MySQL/Chroma 权威关系，也不删除 Gradio。

### 实施顺序

1. 增加文档类型和 multipart 上传、列表、删除客户端契约。
2. 实现列表加载、上传中占位、错误映射和删除失败恢复；首屏列表完成前禁用上传，避免旧响应覆盖新条目。
3. 增加文档工作区、三项导航、桌面/移动布局、空/加载/处理状态和删除确认。
4. 增加 React 行为测试与隔离 Edge E2E，真实完成上传、索引、删除和自动清理。

### 验收标准

- 上传成功后无需刷新即可进入列表；重复、超限、不支持格式和处理失败有明确提示。
- 删除采用确认和乐观更新，失败时恢复原条目；处理中的临时条目不可删除。
- 1440/768/320 px 无横向溢出，控制台无 error/warning，浏览器存储和 URL 无凭据。
- React 全量测试、生产构建、Python 全量回归、真实文档 E2E 和 `git diff --check` 通过。

## 12. Phase 7：React 独立运行与原型前端退役

### 目标与边界

React 已覆盖问答、笔记和文档管理后，移除重复的原型前端及其专用聚合接口。保留 FastAPI 的 REST/SSE 契约、Web HttpOnly 会话、Bearer 服务端认证和所有用户权威数据；不迁移或改写 MySQL、Redis、Chroma 与用户文件。

### 状态流与取舍

```text
start.bat
  -> launcher.py 生成单次启动凭证
  -> 并行启动 FastAPI 与 React/Vite
  -> FastAPI lifespan 幂等创建 APP_USER_ID 固定用户
  -> 浏览器用单次凭证换取 HttpOnly 会话
  -> React 直接消费 REST/SSE
```

- 用户初始化属于服务端不变量，在 FastAPI 启动时完成，不依赖任何页面访问。
- 删除只服务旧前端的 `/api/v1/users` 与 `/api/v1/bootstrap`，减少无调用方的 API 面。
- 保留 `httpx` 作为 FastAPI/Starlette 测试客户端的直接测试依赖；删除不再使用的 UI 依赖。
- 第一阶段规格保留为带归档标记的历史记录，现行 README 与任务计划统一更新为 React 架构。

### 验收标准

- `start.bat` 只启动 FastAPI 与 React，首次数据库状态无需访问旧页面即可使用。
- 运行时代码、依赖、环境示例和现行文档不再包含已退役前端入口。
- Python/React 全量测试、生产构建、源码编译、依赖一致性、离线评测和真实浏览器主工作区验收通过。
- 两阶段形成独立中文 Git 提交，不提交未跟踪的用户文件，不自动 push。
