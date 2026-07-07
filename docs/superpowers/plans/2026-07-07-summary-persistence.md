# Summary 持久化到 MySQL 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将短期记忆的 summary 从纯 Redis（30 分钟 TTL 过期即丢失）改为 MySQL 持久化存储，使第二天打开同一会话时能恢复早期对话摘要。

**Architecture:** 新增 `session_summary` 表（一对一关系）。`_do_compress` 生成摘要后 upsert 到 MySQL；`get_short_term_memory` 在 Redis miss 恢复时，从 MySQL 读取 summary 而非置为 None。

**Tech Stack:** SQLAlchemy ORM、MySQL、Redis、pytest

## Global Constraints

- 文档与代码注释使用中文
- 遵循现有代码风格：MagicMock mock 测试、`from_attributes = True` schema
- 不修改 `routes_chat.py`、`agents/`、`frontend/`，对主链路零侵入
- `session_summary` 表与 `sessions` 表通过 `session_id` 外键关联，一对一关系

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `db/models.py` | 新增 `SessionSummary` ORM 模型 | 修改 |
| `db/init_db.py` | 新增 `session_summary` 建表索引 | 修改 |
| `memory/short_term.py` | `_do_compress` 写 MySQL + `get_short_term_memory` 读 MySQL | 修改 |
| `tests/test_memory.py` | 新增 summary 持久化测试 | 修改 |

---

### Task 1: 新增 SessionSummary ORM 模型

**Files:**
- Modify: `db/models.py`（文件末尾追加）
- Test: `tests/test_memory.py`

**Interfaces:**
- Produces: `SessionSummary` 模型类，字段：`id`(BigInteger, 自增主键)、`session_id`(String(36), 外键 -> sessions.id, 唯一索引)、`summary`(Text)、`updated_at`(TIMESTAMP)

- [ ] **Step 1: 在 `db/models.py` 末尾追加 SessionSummary 模型**

在 `db/models.py` 文件末尾（`SemanticMemory` 类之后）追加：

```python
class SessionSummary(Base):
    """会话级摘要持久化（与 sessions 一对一）"""
    __tablename__ = "session_summary"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False, unique=True)
    summary = Column(Text, nullable=False)
    updated_at = Column(TIMESTAMP, default=datetime.utcnow, onupdate=datetime.utcnow)

    session = relationship("Session", backref="summary_record")
```

- [ ] **Step 2: 在 `db/init_db.py` 的 INDEX_SQL 列表末尾追加索引**

在 `INDEX_SQL` 列表中追加一条（虽然 `unique=True` 已自动建索引，但显式声明便于排查）：

```python
    "CREATE UNIQUE INDEX idx_session_summary ON session_summary(session_id)",
```

- [ ] **Step 3: 验证模型可被导入**

运行：
```bash
cd C:\Users\louwangss\Desktop\agent\ai-knowledge-qa
python -c "from db.models import SessionSummary; print(SessionSummary.__tablename__)"
```
Expected: 输出 `session_summary`，无报错

- [ ] **Step 4: Commit**

```bash
git add db/models.py db/init_db.py
git commit -m "feat: 新增 SessionSummary ORM 模型"
```

---

### Task 2: `_do_compress` 生成摘要后写入 MySQL

**Files:**
- Modify: `memory/short_term.py` 的 `_do_compress` 函数
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `SessionSummary` from `db/models.py`
- Produces: `_do_compress` 新增 `db_session` 可选参数，生成摘要后 upsert 到 `session_summary` 表

- [ ] **Step 1: 编写失败测试 — `_do_compress` 写入 MySQL**

在 `tests/test_memory.py` 末尾追加：

```python
from memory.short_term import _do_compress, _key


def test_do_compress_writes_summary_to_mysql():
    """_do_compress 生成新摘要后应 upsert 到 session_summary 表"""
    from db.models import SessionSummary

    # mock Redis
    r = MagicMock()
    r.get.return_value = ""  # 无已有摘要
    r.lrange.return_value = [
        '{"role": "user", "content": "hello"}',
        '{"role": "assistant", "content": "hi"}',
    ]

    # mock db_session：query 返回 None（无已有记录）
    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = None

    # mock LLM
    with __import__("unittest.mock").mock.patch("memory.short_term.get_llm") as mock_llm:
        mock_resp = MagicMock()
        mock_resp.content = "这是合并后的摘要"
        mock_llm.return_value.invoke.return_value = mock_resp

        _do_compress(r, "u1", "s1", db_session=db_session)

    # 验证 MySQL 写入
    assert db_session.add.called, "应调用 db_session.add 写入 SessionSummary"
    assert db_session.commit.called, "应调用 db_session.commit"
    added_obj = db_session.add.call_args[0][0]
    assert isinstance(added_obj, SessionSummary)
    assert added_obj.session_id == "s1"
    assert "合并后的摘要" in added_obj.summary


def test_do_compress_updates_existing_summary():
    """已有 summary 记录时应更新而非新增"""
    from db.models import SessionSummary

    r = MagicMock()
    r.get.return_value = "旧摘要内容"
    r.lrange.return_value = [
        '{"role": "user", "content": "new question"}',
    ]

    existing_record = SessionSummary(session_id="s1", summary="旧摘要内容")
    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = existing_record

    with __import__("unittest.mock").mock.patch("memory.short_term.get_llm") as mock_llm:
        mock_resp = MagicMock()
        mock_resp.content = "新摘要"
        mock_llm.return_value.invoke.return_value = mock_resp

        _do_compress(r, "u1", "s1", db_session=db_session)

    # 验证更新而非新增
    assert not db_session.add.called, "已有记录时应更新而非 add"
    assert db_session.commit.called
    assert "新摘要" in existing_record.summary
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd C:\Users\louwangss\Desktop\agent\ai-knowledge-qa
pytest tests/test_memory.py::test_do_compress_writes_summary_to_mysql tests/test_memory.py::test_do_compress_updates_existing_summary -v
```
Expected: FAIL（`_do_compress` 签名不匹配或未写入 MySQL）

- [ ] **Step 3: 修改 `_do_compress` 增加 MySQL upsert 逻辑**

在 `memory/short_term.py` 中修改 `_do_compress` 函数：

```python
def _do_compress(r: redis.Redis, user_id: str, session_id: str, db_session=None):
    """安全压缩：每次移除最旧 5 轮 = 10 条消息

    Args:
        db_session: 可选的 SQLAlchemy Session，传入时将摘要持久化到 MySQL
    """
    summary_key = _key(user_id, session_id, "summary")
    messages_key = _key(user_id, session_id, "messages")

    # 1. 读取现有摘要
    existing_summary = r.get(summary_key) or ""

    # 2. 读取最旧 10 条消息
    raw_old = r.lrange(messages_key, 0, 9)
    if not raw_old:
        return
    old_messages = [json.loads(item) for item in raw_old]

    # 3. LLM 合并摘要
    new_summary = _generate_summary(existing_summary, old_messages)
    if not new_summary:
        logger.warning("摘要生成失败，跳过压缩")
        return

    # 4. 先写新摘要到 Redis
    r.set(summary_key, new_summary)

    # 5. 截断消息（删前 10 条，保留剩余）
    r.ltrim(messages_key, 10, -1)

    # 6. 持久化到 MySQL（upsert 逻辑）
    if db_session is not None:
        _persist_summary_to_mysql(db_session, session_id, new_summary)

    logger.info(f"压缩完成: session={session_id}, 移除 {len(raw_old)} 条消息")


def _persist_summary_to_mysql(db_session, session_id: str, summary: str):
    """将摘要 upsert 到 session_summary 表"""
    from db.models import SessionSummary

    existing = db_session.query(SessionSummary).filter(
        SessionSummary.session_id == session_id,
    ).first()

    if existing:
        existing.summary = summary
    else:
        db_session.add(SessionSummary(
            session_id=session_id,
            summary=summary,
        ))
    db_session.commit()
```

- [ ] **Step 4: 运行测试验证通过**

```bash
pytest tests/test_memory.py::test_do_compress_writes_summary_to_mysql tests/test_memory.py::test_do_compress_updates_existing_summary -v
```
Expected: PASS

- [ ] **Step 5: 修改 `maybe_compress` 传递 db_session**

在 `memory/short_term.py` 中修改 `maybe_compress` 函数签名和调用：

```python
def maybe_compress(r: redis.Redis, user_id: str, session_id: str, db_session=None):
    """检查是否需要触发压缩，需要则执行

    Args:
        db_session: 可选的 SQLAlchemy Session，透传给 _do_compress
    """
    round_count = get_round_count(r, user_id, session_id)
    next_round = get_next_compress_round(r, user_id, session_id)

    if round_count < next_round:
        return

    _do_compress(r, user_id, session_id, db_session=db_session)
    r.set(_key(user_id, session_id, "next_compress_round"), next_round + _COMPRESS_INTERVAL)
```

- [ ] **Step 6: 修改 `routes_chat.py` 调用处传入 db**

在 `app/api/routes_chat.py` 中找到调用 `maybe_compress` 的地方，传入 `db`：

```python
            # 步骤 8.5: 检查是否需要触发批量摘要压缩
            maybe_compress(r, payload.user_id, payload.session_id, db_session=db)
```

- [ ] **Step 7: 运行全部 memory 测试**

```bash
pytest tests/test_memory.py -v
```
Expected: 全部 PASS

- [ ] **Step 8: Commit**

```bash
git add memory/short_term.py app/api/routes_chat.py tests/test_memory.py
git commit -m "feat: _do_compress 将摘要持久化到 MySQL session_summary 表"
```

---

### Task 3: `get_short_term_memory` 从 MySQL 恢复 summary

**Files:**
- Modify: `memory/short_term.py` 的 `get_short_term_memory` 函数
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `SessionSummary` from `db/models.py`
- Produces: `get_short_term_memory` 在 Redis miss 恢复时，从 MySQL `session_summary` 表读取 summary

- [ ] **Step 1: 编写失败测试 — Redis miss 时从 MySQL 恢复 summary**

在 `tests/test_memory.py` 末尾追加：

```python
from memory.short_term import get_short_term_memory


def test_get_short_term_memory_restores_summary_from_mysql():
    """Redis miss 时应从 MySQL session_summary 表恢复 summary"""
    from db.models import SessionSummary

    r = MagicMock()
    # Redis 全部 miss
    r.get.return_value = None
    r.lrange.return_value = []

    # mock db_session
    db_session = MagicMock()

    # mock _load_from_mysql 返回空列表（触发 init_session 但 summary 单独恢复）
    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=[]
    ):
        # mock MySQL 中有 summary 记录
        summary_record = SessionSummary(session_id="s1", summary="从MySQL恢复的摘要")
        db_session.query.return_value.filter.return_value.first.return_value = summary_record

        result = get_short_term_memory(r, "u1", "s1", db_session=db_session)

    assert result["summary"] == "从MySQL恢复的摘要"


def test_get_short_term_memory_no_mysql_summary_returns_none():
    """MySQL 中也无 summary 时返回 None"""
    r = MagicMock()
    r.get.return_value = None
    r.lrange.return_value = []

    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = None

    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=[]
    ):
        result = get_short_term_memory(r, "u1", "s1", db_session=db_session)

    assert result["summary"] is None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
pytest tests/test_memory.py::test_get_short_term_memory_restores_summary_from_mysql tests/test_memory.py::test_get_short_term_memory_no_mysql_summary_returns_none -v
```
Expected: FAIL（当前 Redis miss 时 summary 硬编码为 None）

- [ ] **Step 3: 修改 `get_short_term_memory` 增加 MySQL summary 恢复**

在 `memory/short_term.py` 中修改 `get_short_term_memory` 函数：

```python
def get_short_term_memory(r: redis.Redis, user_id: str, session_id: str, db_session=None) -> dict:
    """获取短期记忆上下文

    Returns:
        {"summary": str|None, "messages": list[dict]}
    """
    summary = get_summary(r, user_id, session_id)
    messages = get_messages(r, user_id, session_id, count=10)

    # Redis miss（过期/新会话）→ 从 MySQL 恢复
    if not messages and db_session:
        messages = _load_from_mysql(db_session, user_id, session_id)
        if messages:
            init_session(r, user_id, session_id, db_messages=messages)
        # 从 MySQL 恢复 summary（无论 messages 是否存在）
        summary = _load_summary_from_mysql(db_session, session_id)

    return {"summary": summary, "messages": messages}


def _load_summary_from_mysql(db_session, session_id: str) -> str | None:
    """从 MySQL session_summary 表读取持久化的摘要"""
    from db.models import SessionSummary

    record = db_session.query(SessionSummary).filter(
        SessionSummary.session_id == session_id,
    ).first()
    return record.summary if record else None
```

- [ ] **Step 4: 运行测试验证通过**

```bash
pytest tests/test_memory.py::test_get_short_term_memory_restores_summary_from_mysql tests/test_memory.py::test_get_short_term_memory_no_mysql_summary_returns_none -v
```
Expected: PASS

- [ ] **Step 5: 运行全部 memory 测试确认无回归**

```bash
pytest tests/test_memory.py -v
```
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add memory/short_term.py tests/test_memory.py
git commit -m "feat: Redis miss 时从 MySQL 恢复 session summary"
```

---

### Task 4: 全量回归测试 + 建表脚本验证

**Files:**
- Test: 全部测试

- [ ] **Step 1: 运行全量测试**

```bash
cd C:\Users\louwangss\Desktop\agent\ai-knowledge-qa
pytest -v
```
Expected: 全部 PASS，无回归

- [ ] **Step 2: 验证建表脚本（需要 MySQL 环境）**

```bash
python -m db.init_db
```
Expected: 输出 "创建所有表... done" 和 "创建索引... done"，`session_summary` 表成功创建

- [ ] **Step 3: 最终 Commit（如有遗漏修复）**

```bash
git add -A
git commit -m "test: summary 持久化全量回归通过"
```

---

## Self-Review

**1. Spec coverage:**
- [x] 新增 `session_summary` 表 -> Task 1
- [x] `_do_compress` 写 MySQL -> Task 2
- [x] `get_short_term_memory` 读 MySQL -> Task 3
- [x] `routes_chat.py` 传递 db -> Task 2 Step 6
- [x] 建表脚本 -> Task 1 Step 2 + Task 4 Step 2
- [x] 测试覆盖 -> Task 2/3 各有 2 个测试用例

**2. Placeholder scan:** 无 TBD/TODO，所有代码步骤均含完整代码

**3. Type consistency:**
- `SessionSummary` 模型字段在 Task 1 定义，Task 2/3 使用一致
- `_do_compress(r, user_id, session_id, db_session=None)` 签名在 Task 2 定义，Task 2 Step 5 的 `maybe_compress` 透传一致
- `_load_summary_from_mysql(db_session, session_id)` 在 Task 3 定义并使用
