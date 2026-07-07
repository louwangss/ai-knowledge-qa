# AI 知识库问答系统 - 实现计划

> 技术栈：Python / FastAPI / MySQL / Redis / LangChain / LangGraph / ChromaDB / Gradio / DeepSeek

---

## 模块一：RAG 系统

### 1.1 文档加载（rag/loader.py）

支持 5 种格式（6 个后缀，.html 和 .htm 同格式），根据文件后缀自动选择 Loader：

```python
# 格式 -> Loader 映射
LOADERS = {
    ".pdf":  PyMuPDFLoader,          # pymupdf
    ".docx": Docx2txtLoader,
    ".md":   UnstructuredMarkdownLoader,
    ".html": BSHTMLLoader,
    ".htm":  BSHTMLLoader,
    ".txt":  TextLoader,
}

def load_document(file_path: str) -> list[Document]:
    """根据后缀自动选择 Loader，返回 Document 列表"""
    ext = Path(file_path).suffix.lower()
    loader_class = LOADERS.get(ext)
    if not loader_class:
        raise ValueError(f"不支持的文件格式: {ext}")
    loader = loader_class(file_path)
    return loader.load()
```

### 1.2 文档分块（rag/splitter.py）

固定参数，不给用户调：

```python
def split_text(documents: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )
    return splitter.split_documents(documents)
```

### 1.3 向量化和存储（rag/vector_store.py）

嵌入模型用本地 HuggingFace 模型：

```python
def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-zh-v1.5",
        encode_kwargs={"normalize_embeddings": True},
    )
```

Chroma 存储设计：
- RAG 文档库：单 collection `rag_documents`，用 metadata `where={"user_id": xxx}` 隔离用户
- 语义记忆库：单 collection `semantic_memory`，用 metadata `where={"user_id": xxx}` 隔离用户

文档向量和笔记向量的 metadata 结构不同，通过 type 字段区分：

文档向量 metadata：
```python
{
    "user_id": "xxx",           # 用户隔离
    "mysql_id": "uuid-string",  # 对应 MySQL 主键 UUID（同步用）
    "type": "document",         # 类型标记
    "source": "Happy-LLM.pdf", # 来源文件名
    "created_at": "2026-07-03T14:30:00",
}
```

笔记向量 metadata：
```python
{
    "user_id": "xxx",           # 用户隔离
    "mysql_id": 42,             # 对应 MySQL 主键（同步用）
    "type": "note",             # 类型标记
    "concept": "注意力机制",     # 笔记的概念标签（来自 semantic_memory 表）
    "created_at": "2026-07-03T14:30:00",
}
# 文档有 source 无 concept，笔记有 concept 无 source
# 检索笔记时 concept 直接从 Chroma metadata 返回，不需要回查 MySQL
```

### 1.4 检索组件（rag/retriever.py）

RAG 模块只提供基础组件，上层自由组装（方案 B）：

```python
# 基础检索器（普通问答文档检索 + 深度研究 Agent B 都用这个）
def get_basic_retriever(vector_store, user_id: str, top_k: int = 3, doc_type: str = "document"):
    """返回一个基础向量检索器

    Args:
        doc_type: "document" 只检索文档，"note" 只检索笔记
    """
    return vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={"k": top_k, "filter": {"user_id": user_id, "type": doc_type}},
    )

# 高级检索器（可选增强，默认不启用）
# 注意：深度模式下拆解 Agent 已做查询扩展，Agent B 用 basic_retriever 即可
# 双层扩展（拆解 Agent + MultiQuery）会导致查询爆炸（3 子问题 x 3 扩展 = 9 次查询 x top3 = 27 段文本）
# 仅在普通问答模式想增强检索效果时考虑使用
def get_advanced_retriever(vector_store, llm, user_id: str, top_k: int = 3):
    """返回带 MQE + HyDE 的高级检索器（可选，默认不启用）"""
    mq_retriever = MultiQueryRetriever.from_llm(
        llm=llm,
        retriever=get_basic_retriever(vector_store, user_id, top_k),
    )
    return mq_retriever

# 笔记检索：直接用 get_basic_retriever(vector_store, user_id, top_k, doc_type="note")
```

### 1.5 LLM 配置（rag/llm.py）

```python
def get_llm(temperature: float = 0.3):
    return ChatOpenAI(
        model="deepseek-chat",    # 注意：2026-07-24 后可能改名，实现时查最新文档
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        temperature=temperature,
        streaming=True,            # 支持流式输出
    )
```

DeepSeek context window = 128K token，预算充足。

---

## 模块二：记忆系统

### 2.1 记忆架构总览

```
记忆系统
|
|-- 短期记忆（Redis = MySQL 的读缓存）
|   |-- summary  (String)   旧对话的压缩摘要
|   |-- messages (List)     最近 N 轮原始消息
|   |-- round_count (String) 当前轮数
|   |-- TTL: 30 分钟，用户活跃时续期
|   |-- 压缩：首次在第 11 轮触发，之后每 5 轮一次
|
|-- 对话历史（MySQL，source of truth）
|   |-- 每轮对话自动写入
|
|-- 语义记忆
|   |-- 主存储：MySQL（semantic_memory 表）
|   |-- 索引层：Chroma（semantic_memory collection）
|   |-- 写入时机：用户主动保存笔记
|   |-- 去重：写入前相似度 > 0.9 提示用户
|
|-- 情景记忆（MySQL，episodic_memory 表）
|   |-- 写入时机：关键事件触发（document_loaded / qa_completed / note_saved）
|   |-- 去重：同事件类型 + 同问题文本 + 30 秒内只记一次
|
|-- 检索策略：并行检索，不做路由
    |-- Chroma 文档检索（top 3）
    |-- Chroma 笔记检索（top 3, type=note）
    |-- MySQL 情景记忆（最近 5 条）
    |-- Redis 短期记忆（摘要 + 最近 5 轮）
    |-- 全部并行查，合并后拼入 prompt，LLM 自行取舍
```

### 2.2 短期记忆（memory/short_term.py）

Redis 数据结构：
```
qa:{user_id}:session:{session_id}:summary      -> String    旧对话的压缩摘要（~500 token）
qa:{user_id}:session:{session_id}:messages     -> List      最近 N 轮原始消息（JSON 格式）
qa:{user_id}:session:{session_id}:round_count  -> String    当前轮数（整数）
qa:{user_id}:session:{session_id}:next_compress_round -> String  下次触发压缩的轮数（初始 11）
```

每条消息的 JSON 格式：
```json
{"role": "user", "content": "Transformer 是什么"}
{"role": "assistant", "content": "Transformer 是一种神经网络架构..."}
```

核心操作：
```python
# 追加消息（user 和 assistant 都要 RPUSH）：O(1)
r.rpush(messages_key, json.dumps({"role": "user", "content": "..."}))
r.rpush(messages_key, json.dumps({"role": "assistant", "content": "..."}))

# 读取最近 N 条：O(N)
r.lrange(messages_key, -10, -1)

# 弹出最旧做压缩：O(1)
r.lpop(messages_key)

# 读/写摘要：O(1)
r.get(summary_key)
r.set(summary_key, "...")

# 续期（用户活跃时）：四个 key 统一续期
for key in [summary_key, messages_key, round_count_key, next_compress_round_key]:
    r.expire(key, 1800)  # 30 分钟
```

写入流程：
```
用户发消息 ->
  1. 先写 MySQL chat_history（source of truth），user 消息
  2. 再写 Redis messages，RPUSH user 消息
  3. round_count + 1
  4. 统一续期 EXPIRE 30 分钟
  如果步骤 2 失败：不影响主流程，记录日志，下次从 MySQL 恢复
```

LLM 生成回答后：
```
  5. 写 MySQL chat_history，assistant 消息
  6. RPUSH assistant 消息到 Redis messages（补全对话上下文）
```

读取流程：
```
获取短期记忆 ->
  1. 先查 Redis
     -> Redis 有数据：直接返回
     -> Redis 没数据（过期/新会话/重启）：
        -> 从 MySQL 加载该用户全局最近 5 轮对话（跨 session，user + assistant 成对，正序）
          SELECT * FROM (
              SELECT * FROM chat_history
              WHERE user_id = ?
              ORDER BY created_at DESC LIMIT 10
          ) AS recent ORDER BY created_at ASC;
        -> 写入 Redis -> 返回
```

### 2.3 批量摘要压缩

Session 生命周期：
```
首次打开（Gradio 页面加载时调 POST /api/v1/sessions）-> 创建 session，轮数 = 0
每轮对话 -> 轮数 + 1（一轮 = user 消息 + assistant 回复）
Redis TTL 30 分钟过期 -> session 结束，Redis 清空
用户回来 -> 新 session，轮数从 0 开始
  -> 从 MySQL 加载全局最近 5 轮（跨 session）作为初始上下文
  -> round_count 初始化为实际加载轮数（最多 5）
  -> 新对话从 round_count + 1 开始
  -> SET next_compress_round = 11（初始化压缩触发点）
摘要不跨 session 继承 -> 新 session 从干净状态开始
```

⚠️ 并发说明：当前设计没有加 Redis 分布式锁。RPUSH → round_count+1 → 检查压缩不是原子操作，两个并发请求可能同时触发压缩。单用户场景下不会触发（一个用户不会同时发两条消息），实现时加注释说明此限制即可。多用户场景如需支持，考虑用 Lua 脚本或 Redis SETNX 实现简单锁。

压缩触发逻辑（通过 next_compress_round key 控制）：
```
新 session 初始化时：SET next_compress_round = 11
每轮对话结束后（步骤 8.5）：
  读取 next_compress_round
  如果 round_count < next_compress_round → 不压缩
  如果 round_count >= next_compress_round → 触发压缩：
    -> LRANGE 读取待压缩消息 → LLM 摘要 → SET summary → LTRIM 截断
    -> SET next_compress_round = next_compress_round + 5
```

压缩周期示意（初始 next_compress_round = 11）：
```
轮数 1~10：正常使用
轮数 11：触发压缩，next_compress_round 更新为 16
轮数 12~15：正常使用（摘要 + 最近的原始消息）
轮数 16：再触发压缩，next_compress_round 更新为 21
...以此类推，每 5 轮一次
```

安全压缩流程（每次压缩固定移除最旧的 5 轮 = 10 条消息，LTRIM 边界恒定）：
```
1. GET summary（读取现有摘要，首次为空）
2. LRANGE messages_key 0 9（读取最旧 10 条，不删除）
3. LLM 合并摘要（现有摘要 + 10 条消息 → 新摘要）
4. 摘要成功 → SET summary（先写新摘要）
5. SET 成功 → LTRIM messages_key 10 -1（删前 10 条，保留剩余）
   如果 SET 成功但 LTRIM 失败：消息和摘要都在，下次重试 LTRIM 幂等（前 10 条不变）
   如果 LTRIM 成功但 SET 失败：消息删了但摘要没写，上下文断裂 → 必须先 SET 后 LTRIM
6. SET next_compress_round = next_compress_round + 5
7. 摘要失败 → 消息还在 Redis 里，下次重试
```

为什么 LTRIM 10 -1 是固定的：
- 每次压缩间隔恰好 5 轮 = 10 条消息
- 上次 LTRIM 后 Redis 中最前面的就是下次要压缩的
- 无论初始加载几轮，到 round 11 时 Redis 总有 ≥ 22 条，压缩最旧 10 条永远正确

摘要内容前加衰减提示：
```
[以下为早期对话的摘要，可能遗漏部分细节]
用户和AI讨论了Transformer架构、注意力机制原理...
```

### 2.4 长期记忆

#### 语义记忆（memory/semantic.py）

MySQL 是 source of truth，Chroma 是索引层。

写入笔记流程（仅 POST 新建时做相似度检测，PUT 编辑时不做）：
```
1. 用户保存笔记（POST 新建）
2. 写入前先在 Chroma 做相似度检索
   -> 相似度 > 0.9：后端返回 409 + 已有笔记信息，Gradio 弹 gr.Info 提示"已有类似笔记"，不写入
   -> 相似度 <= 0.9：继续
3. MySQL INSERT（chroma_id = NULL）
4. Chroma ADD（含 metadata: user_id, mysql_id, type="note", concept, created_at）
5. 成功 -> MySQL UPDATE chroma_id = xxx
6. 失败 -> 记录日志，chroma_id 保持 NULL
7. 后台补偿任务定期扫描 WHERE chroma_id IS NULL，补写到 Chroma

后台补偿任务实现方案（FastAPI lifespan）：
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(compensation_loop())
    yield
    task.cancel()

async def compensation_loop():
    while True:
        await asyncio.sleep(300)  # 5 分钟
        # 扫描 semantic_memory WHERE chroma_id IS NULL
        # 对每条记录：先查 Chroma 是否已有该 mysql_id 的向量（防止步骤 5 失败后重复写入）
        #   -> 已有：直接 UPDATE chroma_id，跳过 ADD
        #   -> 没有：Chroma ADD -> MySQL UPDATE chroma_id
```

在 app/main.py 中：`app = FastAPI(lifespan=lifespan)`

注意：PUT 编辑笔记不走相似度检测——用户编辑已有笔记补充内容是合理需求，
      不应被相似度检测挡住。只有 POST 新建时检测无意重复。
```

编辑笔记流程：
```
1. 更新 MySQL（content, concept, updated_at）
2. 删除 Chroma 旧向量（通过 metadata 中的 mysql_id 定位）
3. 重新 embed 写入 Chroma（获取新 chroma_id）
4. MySQL UPDATE chroma_id = 新值
```

删除笔记流程：
```
1. 删除 MySQL 记录
2. 删除 Chroma 对应向量（通过 metadata 中的 mysql_id 定位）
```

Chroma 损坏 / 换 embedding 模型时：
```
从 MySQL 全量重新构建 Chroma 索引
```

#### 情景记忆（memory/episodic.py）

写入时机（仅限关键事件）：
```
- document_loaded:  文档加载成功 -> "用户在14:30加载了文档《Happy-LLM》"
- qa_completed:     一次完整问答完成 -> "用户提问了关于Transformer的问题，基于文档第3章给出了回答"
- note_saved:       用户保存笔记 -> "用户保存了关于注意力机制的学习笔记"
```

去重逻辑（按事件类型分两种策略）：
```
qa_completed 事件（有 question_text）：
  写入前查询 MySQL:
    SELECT 1 FROM episodic_memory
    WHERE user_id = ? AND event_type = 'qa_completed' AND question_text = ?
    AND created_at > NOW() - INTERVAL 30 SECOND
  查到了 -> 跳过（重复触发，如刷新页面/双击发送）
  没查到 -> 写入

document_loaded / note_saved 事件（没有 question_text，用 content 去重）：
  写入前查询 MySQL:
    SELECT 1 FROM episodic_memory
    WHERE user_id = ? AND event_type = ? AND content = ?
    AND created_at > NOW() - INTERVAL 30 SECOND
  查到了 -> 跳过
  没查到 -> 写入

注意：不用 question_text = NULL 做去重——SQL 中 NULL = NULL 返回 NULL（不为真），
      去重完全失效。按事件类型用各自有意义的字段去重。
```

### 2.5 并行检索

```python
async def retrieve_context(user_id, session_id, question, vector_store, db, redis, mode="normal"):
    """并行检索所有记忆源，合并返回

    Args:
        mode: "normal" 执行全部 4 路；"deep" 跳过文档检索（a），只执行笔记/情景/短期记忆
    注：
        - Chroma 检索是同步 CPU 密集操作，用 asyncio.to_thread 包装避免阻塞事件循环
        - MySQL/Redis 操作若用同步驱动，同样需要 to_thread 包装
        - 用 asyncio.gather 并行执行，总延迟 ≈ max(各路延迟)
    """
    async def search_chroma_async(vs, uid, q, top_k, doc_type):
        return await asyncio.to_thread(_sync_chroma_search, vs, uid, q, top_k, doc_type)

    if mode == "normal":
        doc_results, note_results, episodic_results, short_term = await asyncio.gather(
            search_chroma_async(vector_store, user_id, question, top_k=3, doc_type="document"),
            search_chroma_async(vector_store, user_id, question, top_k=3, doc_type="note"),
            asyncio.to_thread(search_episodic_memory, db, user_id, limit=5),
            asyncio.to_thread(get_short_term_memory, redis, user_id, session_id),
        )
    else:  # deep 模式跳过文档检索（Agent B 会做更细粒度的检索）
        note_results, episodic_results, short_term = await asyncio.gather(
            search_chroma_async(vector_store, user_id, question, top_k=3, doc_type="note"),
            asyncio.to_thread(search_episodic_memory, db, user_id, limit=5),
            asyncio.to_thread(get_short_term_memory, redis, user_id, session_id),
        )
        doc_results = []

    # 全部合并拼入 prompt，LLM 自行取舍
    return {
        "documents": doc_results,
        "notes": note_results,
        "episodic_memory": episodic_results,
        "short_term_memory": short_term,
    }
```

不做路由，两条路都走，让 LLM 自己从合并后的结果里选有用的。

### 2.6 Session 状态同步

Redis 过期后 MySQL sessions 表的状态处理：
```
- status 字段：预留字段，当前仅有 'active' 默认值，关闭/结束功能后续扩展
- 判断 session 是否在 Redis 中活跃：不看 status，看 last_active
  WHERE last_active > NOW() - INTERVAL 30 MINUTE
- 每次用户发消息时 UPDATE last_active = NOW()
- status 不会产生脏数据，last_active 始终准确
```

---

## 模块三：MySQL 数据库

### 3.1 完整建表语句

-- 最低要求：MySQL 5.7+（多列 TIMESTAMP DEFAULT CURRENT_TIMESTAMP 需要 5.6.5+）

```sql
-- 1. 用户表
CREATE TABLE users (
    id          VARCHAR(36) PRIMARY KEY,
    username    VARCHAR(50) NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. 会话表
CREATE TABLE sessions (
    id            VARCHAR(36) PRIMARY KEY,
    user_id       VARCHAR(36) NOT NULL,
    status        VARCHAR(20) DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. 文档表
CREATE TABLE documents (
    id            VARCHAR(36) PRIMARY KEY,
    user_id       VARCHAR(36) NOT NULL,
    filename      VARCHAR(255) NOT NULL,
    file_type     VARCHAR(20) NOT NULL,
    file_path     VARCHAR(500) NOT NULL,
    file_size     BIGINT,
    chunk_count   INT,
    chunk_size    INT NOT NULL,
    chunk_overlap INT NOT NULL,
    content_hash  CHAR(64) NOT NULL,    -- SHA-256 hex 编码（64 字符）
    status        VARCHAR(20) DEFAULT 'processing',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. 对话历史表
CREATE TABLE chat_history (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id      VARCHAR(36) NOT NULL,
    session_id   VARCHAR(36) NOT NULL,
    role         VARCHAR(20) NOT NULL,
    content      TEXT NOT NULL,
    mode         VARCHAR(20),             # 该轮对话的模式：normal / deep（仅 user 消息记录，assistant 消息为 NULL）
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. 情景记忆表
CREATE TABLE episodic_memory (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id       VARCHAR(36) NOT NULL,
    session_id    VARCHAR(36) NOT NULL,
    event_type    VARCHAR(30) NOT NULL,
    content       TEXT NOT NULL,
    question_text TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. 语义记忆表（笔记的 source of truth）
CREATE TABLE semantic_memory (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id      VARCHAR(36) NOT NULL,
    concept      VARCHAR(100),
    content      TEXT NOT NULL,
    chroma_id    VARCHAR(100),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

### 3.2 索引

```sql
CREATE INDEX idx_chat_session ON chat_history(user_id, session_id, created_at);
CREATE INDEX idx_chat_user_time ON chat_history(user_id, created_at);
CREATE INDEX idx_episodic_dedup ON episodic_memory(user_id, event_type, created_at);
CREATE INDEX idx_episodic_user_time ON episodic_memory(user_id, created_at);
CREATE INDEX idx_semantic_chroma ON semantic_memory(chroma_id);
CREATE INDEX idx_semantic_user ON semantic_memory(user_id);
CREATE UNIQUE INDEX idx_doc_hash ON documents(user_id, content_hash);
CREATE INDEX idx_session_user ON sessions(user_id, status, last_active);
```

---

## 模块四：FastAPI 后端

### 4.1 架构

前后端分离：
```
用户 -> Gradio（薄前端 UI）-> FastAPI（后端 API）-> RAG / 记忆系统 / 多 Agent
```

API 版本前缀：`/api/v1/`

### 4.2 端点清单

| 端点 | 方法 | 功能 | 优先级 |
|------|------|------|--------|
| `/api/v1/users` | POST | 创建用户 | P0 |
| `/api/v1/sessions` | POST | 创建会话 | P0 |
| `/api/v1/documents` | POST | 上传文档 | P0 |
| `/api/v1/documents` | GET | 文档列表（query param: user_id） | P0 |
| `/api/v1/documents/{id}` | DELETE | 删除文档（同步清理 MySQL + Chroma + 本地文件） | P1 |
| `/api/v1/chat` | POST | 问答（mode=normal/deep，SSE 流式） | P0 |
| `/api/v1/chat/history` | GET | 对话历史（query param: user_id, session_id） | P0 |
| `/api/v1/notes` | POST | 保存笔记 | P0 |
| `/api/v1/notes` | GET | 笔记列表（query param: user_id） | P0 |
| `/api/v1/notes/{id}` | PUT | 编辑笔记（同步更新 MySQL + Chroma） | P0 |
| `/api/v1/notes/{id}` | DELETE | 删除笔记（同步删除 MySQL + Chroma） | P0 |
| `/api/v1/memory/recall` | GET | 学习历程 | P1 |
| `/api/v1/stats` | GET | 学习统计 | P2 |
| `/api/v1/report` | GET | 学习报告 | P2 |

> 注：`notes/{id}` 的 `{id}` 是 semantic_memory 表的自增整数主键（非 UUID）。`documents/{id}` 的 `{id}` 是 UUID。

### 4.3 user_id 传递方式

- GET 请求：query parameter `?user_id=xxx`
- POST 请求：request body 里带 `user_id` 字段
- 不需要 header/cookie

### 4.3.1 用户身份管理流程（前端）

项目是单用户学习工具，不做认证系统，用最简方案：

```
首次打开 Gradio ->
  1. 检查 localStorage 是否有 user_id
  2. 没有 -> 调 POST /api/v1/users 创建用户 -> 返回 user_id (UUID)
  3. 存入 localStorage（key: "qa_user_id"）
  4. 后续所有请求带 user_id

session 管理：
  1. 页面加载时调 POST /api/v1/sessions 创建 session，返回 session_id
  2. session_id 存在 Gradio gr.State 中（不需要 localStorage，刷新即新 session）
  3. Redis TTL 30 分钟过期后，下次对话自动创建新 session
```

Gradio 侧实现要点：
```python
import gradio as gr
import httpx, asyncio

async def get_or_create_user():
    """页面加载时调用，获取或创建 user_id"""
    # Gradio 无 localStorage，用 gr.BrowserSession 或启动时弹窗输入
    # 最简方案：用 gr.State 存储，首次用固定 user_id 或简单注册
    # 实现时根据 Gradio 版本选择最佳方案
    pass
```

> 注：Gradio 前端无法直接操作 localStorage。实现时可选方案：
> - 方案 A：用 gr.Textbox 让用户输入/自动生成 user_id，存 gr.State
> - 方案 B：用 Gradio 的 gr.BrowserSession（如果版本支持）
> - 方案 C：前端硬编码一个 user_id（单用户学习工具，够用）
> 推荐方案 C 作为 MVP，后续按需升级。

### 4.4 核心端点：POST /api/v1/chat

请求体：
```json
{
    "user_id": "xxx",
    "session_id": "xxx",
    "message": "Transformer 是什么",
    "mode": "normal"
}
```
mode 取值：`"normal"`（普通问答）或 `"deep"`（深度研究，多 Agent）

返回：SSE 流式（StreamingResponse）

完整内部流程（统一框架，mode 控制分支）：
```
步骤 1:   写 MySQL chat_history（user 消息）
步骤 2:   RPUSH user 消息到 Redis messages
步骤 3:   round_count + 1（不触发压缩，压缩移到步骤 8 后）
步骤 4:   统一续期 Redis TTL 30 分钟（含 next_compress_round key）
步骤 5:   并行检索（asyncio.gather 并行执行，见 2.5）：
            normal 模式：a + b + c + d 全部执行
            deep 模式：跳过 a（文档检索由 Agent B 接管），只执行 b + c + d
            a. Chroma 文档检索（top 3, type=document）— 仅 normal
            b. Chroma 笔记检索（top 3, type=note）
            c. MySQL 情景记忆（最近 5 条）
            d. Redis 短期记忆（摘要 + 最近 5 轮）
            注：normal 模式文档检索 top 3，deep 模式 Agent B 每个子问题 top 3，
                如果 3 个子问题就是 9 段。检索深度不同是有意设计：
                normal 快速回答（2~5 秒），deep 结构化深度分析（10~18 秒）。
步骤 6:   判断 mode（LLM 生成 + Tool Use 是同一个调用，不是两个独立步骤）：
            - normal: 拼上下文 prompt（步骤 5 的 a/b/c/d 结果）+ 工具定义
              -> AgentExecutor -> astream_events
              -> LLM 在生成过程中自主判断是否调 web_search / calculator
              -> SSE 流式返回（token + 工具 status）
              注意：deep 模式不做 tool use，专注本地文档深度分析（有意设计，见 7.x）
            - deep: 将步骤 5 的 b/c/d 结果注入 ResearchState 初始值
              -> LangGraph 多 Agent 流程 -> SSE 流式返回
步骤 7:   写 MySQL chat_history（assistant 消息）
步骤 8:   RPUSH assistant 消息到 Redis messages（补全对话上下文）
步骤 8.5: 检查是否需要触发批量摘要压缩（仅在步骤 6~7 成功后执行）：
          读取 next_compress_round（初始 11）
          如果 round_count >= next_compress_round：
            -> LRANGE 读取待压缩消息 → LLM 摘要 → SET summary → LTRIM 截断
            -> SET next_compress_round = next_compress_round + 5
          压缩放在成功后执行的原因：如果步骤 6 LLM 失败，cleanup 只回滚当前轮，
          不影响已有消息结构。压缩的前提是"这一轮成功了"。
步骤 9:   写 MySQL episodic_memory（qa_completed 事件，含去重检查）
步骤 10:  UPDATE sessions.last_active = NOW()
步骤 11:  统一续期 Redis TTL 30 分钟
```

### 4.5 SSE 流式输出

两种模式都走 SSE，但内容不同：
```
normal 模式：
  -> 直接流式 LLM token（逐字输出）

deep 模式：
  -> 先流式 Agent 状态更新（"正在拆解问题..."、"正在检索文档..."）
  -> 最后流式最终答案的 LLM token
```

使用 sse-starlette 的 EventSourceResponse（见 8.3 节）。

---

## 模块五：Redis 设计

### 5.1 Key 命名规范

冒号分层，从左到右从大到小：
```
qa:{user_id}:session:{session_id}:summary             -> String    旧对话的压缩摘要
qa:{user_id}:session:{session_id}:messages            -> List      最近 N 轮原始消息
qa:{user_id}:session:{session_id}:round_count         -> String    当前轮数
qa:{user_id}:session:{session_id}:next_compress_round -> String    下次触发压缩的轮数
```

SCAN 友好：
- `SCAN MATCH qa:{user_id}:*` 查看某用户所有数据
- `SCAN MATCH qa:{user_id}:session:*` 查看某用户所有会话

### 5.2 TTL 策略

| 数据类型 | TTL | 续期策略 |
|----------|-----|---------|
| summary | 30 分钟 | 用户活跃时续期 |
| messages | 30 分钟 | 用户活跃时续期 |
| round_count | 30 分钟 | 用户活跃时续期 |
| next_compress_round | 30 分钟 | 用户活跃时续期 |

问答缓存：不做（砍掉）。RAG 答案依赖文档上下文，精确匹配命中率低且有正确性风险。

### 5.3 Redis 和 MySQL 的关系

明确：MySQL 是 source of truth，Redis 是 MySQL 的读缓存。

```
写入：先写 MySQL -> 再写 Redis
读取：先查 Redis -> miss 则从 MySQL 加载最近 5 轮（user + assistant 成对）到 Redis
过期：Redis TTL 到期，数据清空，MySQL 保留全量
恢复：用户回来，新 session，从 MySQL 加载最近 5 轮
一致性：Redis 写入失败不影响主流程，MySQL 始终是准的
```

---

## 模块六：多 Agent 协作

### 6.1 编排框架

使用 LangGraph（LangChain 官方编排框架）。
普通问答模式用简单 LangChain Chain，不需要 LangGraph。

### 6.2 触发方式

用户手动选择 mode="deep"，不做自动路由。

### 6.3 共享状态对象

```python
from typing import TypedDict

class RetrievedDoc(TypedDict):
    content: str              # 文档内容
    source: str               # 来源文件名
    sub_question: str         # 回答了哪个子问题
    score: float              # 相关度分数

class RetrievedNote(TypedDict):
    content: str              # 笔记内容
    concept: str              # 笔记的概念标签（来自 semantic_memory 表）

class ResearchState(TypedDict):
    original_question: str    # 原始问题（拆解 Agent 读）
    sub_questions: list[str]  # 子问题列表（拆解 Agent 写，检索 Agent 读）
    retrieved_docs: list[RetrievedDoc]  # 检索结果（检索 Agent 写，总结 Agent 读）
    notes: list[RetrievedNote]          # 笔记检索结果（步骤 5b 注入，总结 Agent 读）
    episodic_memory: list[str]          # 情景记忆（步骤 5c 注入，总结 Agent 读）
    short_term_memory: str              # 短期记忆（步骤 5d 注入，总结 Agent 读）
    final_answer: str         # 最终答案（总结 Agent 写）
```

### 6.4 三个 Agent 的职责

```
Agent A（拆解器）：
  输入：original_question
  输出：sub_questions（3~5 个子问题/搜索关键词）
  示例："Transformer 的原理" -> ["Transformer 的定义", "注意力机制原理", "Transformer 的应用场景"]

Agent B（检索器）：
  输入：sub_questions
  操作：对每个子问题用 asyncio.gather + asyncio.to_thread 并行检索 Chroma（每个 top 3）
        Chroma 是同步 CPU 密集操作，用 to_thread 包装避免阻塞事件循环
        3~5 个子问题并行，总延迟 ≈ 单次检索延迟
        注意：必须加 type="document" 过滤，否则会重复检索到笔记（笔记已在步骤 5b 检索过）
        用 basic_retriever，不用 advanced_retriever
        拆解 Agent 已经做了查询扩展，不需要 MultiQuery 再扩展一层
        双层扩展会导致 3 子问题 x 3 扩展 = 9 次查询 x top3 = 27 段文本，token 预算爆炸
  输出：retrieved_docs（结构化 RetrievedDoc 列表，带 source/sub_question/score）
  去重：所有子问题检索完后，按文档内容去重（同一段文档对多个子问题相关时只保留一次）

Agent C（总结器）：
  输入：original_question + retrieved_docs + notes + episodic_memory + short_term_memory
  操作：按子问题分组组织文档上下文，结合笔记/情景/短期记忆，生成结构化回答
  short_term_memory 格式化（Redis summary + messages → prompt 字符串）：
    如果有 summary：先放摘要段落
    然后放最近 N 轮对话（role: content 逐条拼接）
    示例：
      [早期对话摘要]
      用户和AI讨论了Transformer架构、注意力机制原理...

      [最近对话]
      user: Transformer 的注意力机制是什么？
      assistant: 注意力机制的核心思想是...
  prompt 追加约束：如果 retrieved_docs 为空或与问题无关，
    回答"当前文档库中未找到与该问题相关的内容"，不要编造答案。
    可建议用户上传相关文档或切换到普通问答模式。
  注意：retrieved_docs 为空时不短路，让 Agent C 统一处理（保持流程一致性，
    且笔记/情景记忆可能仍有有用上下文）
  输出：final_answer（流式输出）
```

### 6.5 LangGraph 工作流

```
START -> agent_a_decompose -> agent_b_retrieve -> agent_c_summarize -> END
```

线性流程，不需要条件分支。每个 Agent 是一个 node。

### 6.6 流式输出（决策点 C 已确认）

deep 模式的 SSE 事件序列（6 条 status 全必选，无可选）：

```
status: "正在拆解问题..."           ← Agent A 开始
status: "已拆解为 3 个子问题:\n- Transformer 的定义\n- 注意力机制原理\n- Transformer 的应用场景"  ← Agent A 结束
status: "正在检索文档..."           ← Agent B 开始
status: "已检索到 9 段相关内容"      ← Agent B 结束
status: "正在生成回答..."           ← Agent C 开始
token:  "Trans" / "former" / ...   ← Agent C 流式输出
sources: [{"source": "Happy-LLM.pdf", "score": 0.87}, ...]
done
```

设计决策：

| 决策点 | 决定 | 理由 |
|--------|------|------|
| status 粒度 | 节点级，6 条全必选 | deep 模式总耗时 10~18 秒，只有"正在..."没有"已完成..."用户会以为卡住；带数字的完成提示同时传递进度感和能力感 |
| 子问题展示 | status 里展示具体子问题列表 | 透明度：用户看到系统怎么理解自己的问题，比黑箱好。不是"可以打断"——当前设计无中断机制，价值就是透明度本身 |
| 子问题数据来源 | ResearchState.sub_questions 直接读 | len + 文本拼接，零额外成本 |
| Agent A prompt 约束 | 子问题 10~20 字 | 不撑爆 gr.Markdown 状态栏 |
| 检索结果展示 | status 只显示数量（len(retrieved_docs)） | 来源已有 sources 事件承接，status 不重复。status 管"进度"，sources 管"来源" |

面试话术：
- "拆解过程对用户可见，增强系统透明度和可信度"
- 不说"用户可以判断方向并打断"——当前设计没有中断机制，Agent A 完成后 B 立即开始

技术实现：方向 2（custom stream mode）优先

```
stream_mode=["updates", "custom"] 组合使用
- updates 模式：每个节点完成后推送 state 增量 → 驱动 status（"已拆解为..." / "已检索到..."）
- custom 模式：Agent C 节点内部用 get_stream_writer() 主动推送 token → 驱动逐字输出
- 不依赖 messages 模式的全局过滤，架构上保证干净（A/B 不推 token，只有 C 推）
```

Agent C 节点内部：
```python
from langgraph.config import get_stream_writer

async def agent_c_summarize(state):
    writer = get_stream_writer()
    full_answer = ""
    async for chunk in llm.astream(prompt):
        writer({"type": "token", "content": chunk.content})
        full_answer += chunk.content
    return {"final_answer": full_answer}
```

外层消费（FastAPI SSE 端）：
```python
async for mode, data in graph.astream(input, stream_mode=["updates", "custom"]):
    if mode == "updates":
        # updates 模式返回 {node_name: state_update_dict}，需要遍历
        for node_name, update in data.items():
            if "sub_questions" in update:
                yield {"event": "status", "data": json.dumps({
                    "content": f"已拆解为 {len(update['sub_questions'])} 个子问题:\n"
                               + "\n".join(f"- {q}" for q in update['sub_questions'])
                })}
                yield {"event": "status", "data": json.dumps({"content": "正在检索文档..."})}
            elif "retrieved_docs" in update:
                yield {"event": "status", "data": json.dumps({
                    "content": f"已检索到 {len(update['retrieved_docs'])} 段相关内容"
                })}
                yield {"event": "status", "data": json.dumps({"content": "正在生成回答..."})}
    elif mode == "custom":
        if data["type"] == "token":
            yield {"event": "token", "data": json.dumps({"content": data["content"]})}
```

注意：节点"开始"的 status（"正在拆解..."）在 graph.astream 启动前直接 yield；
      后续节点的"开始" status（"正在检索..."、"正在生成..."）在前一个节点的 updates 收到后立即 yield。

备选方案：
- 方向 1（备选）：messages 模式 + metadata 的 langgraph_node 字段过滤 token 来源
- 方向 3（兜底）：价值低。messages 模式捕获所有 LLM 调用（含 invoke() 的完整 chunk），"天然干净"假设不成立，仍需过滤

⚠️ API 不确定性：get_stream_writer 和 updates 数据结构的具体行为未经联网验证，实现阶段第 0 步必须先验证。

实现阶段第 0 步（API 验证，写代码前先跑）：

```
验证 0：Chroma filter 语法（最高优先级）
  - 确认 ChromaDB 多条件 filter 的正确写法
  - search_kwargs={"filter": {"user_id": xxx, "type": "document"}} 是否被 LangChain 正确转换
  - 如果不行，改用 {"$and": [{"user_id": xxx}, {"type": "document"}]} 语法
  - 测试 user_id 隔离和 type 过滤是否同时生效
  - 此项不通过 = 用户隔离失效，后续所有检索逻辑无意义

验证 1.1：get_stream_writer 单独可用性
  - from langgraph.config import get_stream_writer 能否导入
  - 在简单节点内调用 writer({"type": "token", "content": "test"})
  - 外层 graph.astream(input, stream_mode="custom") 能否收到
  - 如果通过 → 进入验证 1.2

验证 1.2：组合模式 stream_mode=["updates", "custom"]
  - 确认两个模式能同时工作
  - 确认外层收到的数据结构（stream_mode 为列表时 astream 返回 (mode, data) 元组？）
  - 确认 updates 和 custom 事件的交错顺序是否符合预期
  - 如果通过 → 方向 2 定板

验证 1.3：updates 模式的数据结构
  - 确认返回格式是 {node_name: state_update_dict}，不是直接的 state update
  - 代码中用 for node_name, update in data.items() 遍历，不能用 "字段名" in data
  - 打印实际输出确认字段结构

验证 2：messages 模式的 metadata 结构（方向 1 备选验证）
  - 跑一个 2 节点图，stream_mode=["updates", "messages"]
  - 打印每个 message chunk 的 metadata 完整结构
  - 确认 langgraph_node 字段名和值（节点名）
  - 如果字段存在且可靠 → 方向 1 可作为备选

验证 3：invoke() 在 messages 模式下的行为
  - 跑一个节点用 invoke()、另一个用 astream()
  - 观察 invoke() 的输出是否出现在 messages 流中
  - 确认 chunk 结构差异

验证 4：AgentExecutor 构造函数参数
  - 确认 stream_events 不是构造函数参数
  - 通过 .astream_events(version="v2") 方法启用流式事件
  - 确认 max_iterations 参数可用

验证 5：on_tool_start / on_tool_end 的 event 数据结构
  - 确认 astream_events(version="v2") 中 tool 相关事件的 data 字段格式
  - 正确提取工具名和工具输入/输出，驱动 status 栏显示
  - 注意：v2 事件格式与 v1 不同，以 v2 文档为准

验证 6：Chroma filter 语法
  - 确认 ChromaDB 的 metadata filter 用 `{"user_id": xxx}` 还是 `$and` / `$eq` 语法
  - search_kwargs={"filter": {...}} 是否被 LangChain VectorStore 正确转换
  - 多条件 filter（user_id + type）的写法

验证顺序：1.1 → 1.2 → 1.3，通过则跳过 2/3/4（方向 2 定板后其他方向不需要）
```

---

## 模块七：Tool Use

### 7.1 两层架构

```
第一层（pipeline 必经，每次都执行）：
  ├── Chroma 文档检索（top 3）
  ├── Chroma 笔记检索（top 3, type=note）
  ├── MySQL 情景记忆（最近 5 条）
  └── Redis 短期记忆（摘要 + 最近 5 轮）

第二层（tool 可选，LLM 自主决策）：
  ├── web_search（Tavily）— 文档里查不到的信息
  └── calculator — 数学计算
```

RAG 不是 tool，是 pipeline 的必经步骤。
只有 web_search 和 calculator 是 tool。

### 7.2 工具定义

```python
from langchain_core.tools import tool

@tool
def web_search(query: str) -> str:
    """搜索互联网获取最新信息。当文档中没有相关内容或需要最新信息时使用。"""
    tavily = TavilySearchResults(max_results=3)
    results = tavily.invoke(query)
    return str(results)

@tool
def calculator(expression: str) -> str:
    """执行数学计算。当需要数值计算时使用。传入的 expression 应为纯数学表达式（如 '2*3+1'），不包含变量赋值或函数调用。"""
    # 安全要求：禁止用 eval()，用 numexpr 库做数学运算
    # numexpr 只支持数学运算，不能执行任意 Python 代码
    import numexpr
    return str(numexpr.evaluate(expression))
```

### 7.3 触发方式

LLM function calling 自主决策。
LLM 拿到第一层 pipeline 的上下文后，判断是否需要调用额外工具：

```
用户提问 -> [第一层 pipeline 始终执行]
  -> LLM 拿到上下文 + 工具定义
  -> LLM 判断：
     ├── 文档已足够 -> 直接回答
     ├── 需要最新信息 -> 调 web_search -> 补充结果 -> 回答
     └── 需要计算 -> 调 calculator -> 补充结果 -> 回答
```

### 7.4 工具和 RAG 的关系

| 数据特征 | 处理方式 |
|----------|---------|
| 静态文档知识 | RAG 检索（pipeline 必经） |
| 实时信息/最新动态 | web_search tool（LLM 按需调用） |
| 数学计算 | calculator tool（LLM 按需调用） |

### 7.5 面试话术

"我把工具分成两层——RAG 是基础能力，每次请求都执行；web_search 和 calculator 是增强能力，仅 normal 模式下由 LLM 通过 function calling 按需调用。deep 模式专注本地文档的深度分析，不做网络搜索——深度研究的价值在于结构化地分析已有知识，而不是扩大检索范围。"

---

## 模块八：SSE 流式输出

### 8.1 SSE 事件格式

使用 SSE 标准的 `event:` 行 + `data:` JSON 格式：

```
event: status
data: {"content": "正在检索文档..."}

event: token
data: {"content": "Trans"}

event: sources
data: {"content": [{"source": "Happy-LLM.pdf", "score": 0.87}, ...]}

event: done
data: {}

event: error
data: {"content": "DeepSeek API 调用失败"}
```

### 8.2 事件类型清单

| 类型 | 用途 | 说明 |
|------|------|------|
| `status` | 状态更新 | deep 模式 Agent 状态 / normal 模式工具调用中 |
| `token` | 答案 token | LLM 逐字输出 |
| `sources` | 检索来源 | 文档名、相似度分数，展示"答案从哪来" |
| `done` | 流正常结束 | 告诉前端流结束 |
| `error` | 异常 | LLM 调用失败、检索失败、超时 |

### 8.3 FastAPI 端实现

使用 `sse-starlette`（成熟、文档丰富）：

```python
# requirements.txt: sse-starlette>=1.8.0
from sse_starlette.sse import EventSourceResponse

async def chat_endpoint(request):
    async def event_generator():
        try:
            # 步骤 5 并行检索...
            yield {"event": "status", "data": json.dumps({"content": "正在生成回答..."})}
            # LLM 流式输出...
            async for token in llm.astream(...):
                yield {"event": "token", "data": json.dumps({"content": token})}
            # 检索来源
            yield {"event": "sources", "data": json.dumps({"content": sources})}
            yield {"event": "done", "data": "{}"}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"content": str(e)})}
    return EventSourceResponse(event_generator())
```

FastAPI SSE 端点可独立用 curl / Postman 测试，面试好讲。

### 8.4 Gradio 翻译层

架构：Gradio 后端做翻译层，消费 FastAPI SSE，翻译成 Gradio 能理解的内容。

```
浏览器 ↔ Gradio 内部流式协议 ↔ Gradio 后端（async generator）↔ FastAPI SSE 端点 ↔ DeepSeek / ChromaDB / Redis
```

Gradio 后端用 **async generator + httpx.AsyncClient**（以下为伪代码，仅展示 SSE 翻译逻辑；实际 Gradio 事件处理函数的 yield 格式见 9.5 节）：

```python
async def chat_fn(message, history, mode, user_id, session_id):
    """Gradio 后端：调 FastAPI SSE，翻译成 Gradio 渲染"""
    # history 是 gr.Chatbot 的 type="messages" 格式：[{"role": "user/assistant", "content": "..."}]
    # 本轮要 yield 的完整列表 = history + 当前 assistant 回复（逐字更新）
    assistant_msg = {"role": "assistant", "content": ""}
    chatbot_history = history + [assistant_msg]  # 先加空 assistant 占位
    accumulated = ""
    collected_sources = []
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", f"{API_URL}/api/v1/chat", json={...}) as resp:
            async for event in parse_sse_stream(resp):  # 解析 SSE 多行格式
                if event["type"] == "status":
                    status_md = event["content"]  # 更新独立状态栏组件（见 9.5 节），不污染聊天消息
                elif event["type"] == "token":
                    accumulated += event["content"]
                    assistant_msg["content"] = accumulated  # 原地更新最后一条
                    yield chatbot_history           # 替换模式：每次 yield 完整消息列表
                elif event["type"] == "sources":
                    collected_sources = event["content"]
                elif event["type"] == "error":
                    assistant_msg["content"] = accumulated + f"\n\n⚠️ {event['content']}"
                    yield chatbot_history
                    return
                elif event["type"] == "done":
                    if collected_sources:
                        assistant_msg["content"] = accumulated + format_sources(collected_sources)
                        yield chatbot_history
                    return
```

关键设计：

1. **yield 替换模式**：每次 `yield chatbot_history`（完整消息列表），不是 yield 增量。Gradio Chatbot 的流式是替换渲染——每次 yield 覆盖前一次内容。全流程统一替换模式，不混用增量/全量。`gr.Chatbot(type="messages")` 要求消息列表格式 `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`。

2. **status 用独立状态栏（方式 B）**：用独立的 `gr.Markdown` 组件显示状态更新（"正在拆解问题..."、"正在搜索网络..."），**不混入聊天消息**。原因：status 文本如果混进聊天消息，写入 MySQL chat_history 时是脏数据。独立状态栏保证 chat_history 存的是干净回答。

3. **sources 在 done 时输出**：攒到 done 事件，拼在 accumulated 末尾一次性 yield。

4. **async**：用 `async def` + `httpx.AsyncClient` + `async for`，多用户并发不阻塞。面试可讲"Gradio 层用了 async generator + httpx.AsyncClient"。

5. **SSE 解析**：SSE 每条消息由 `event:` 行 + `data:` 行 + 空行组成，`httpx` 的 `aiter_lines()` 逐行返回，需要自行组装。手写解析（逐行读 `event:` 和 `data:` 拼装，约 20 行代码）。

### 8.5 决策点 B：normal 模式 Tool Use 流式处理

**方案选择：Option C — LLM 整合工具结果 + 统一 sources**

流程：
```
normal 模式 tool use 流程：
1. 第一层 pipeline 检索完成（文档/笔记/情景/短期记忆）
2. 构造 prompt（上下文 + 工具定义）-> AgentExecutor -> astream_events
3. LLM 自主判断是否调工具：
   a. 不需要 -> 直接流式 token -> done
   b. 需要 web_search -> on_tool_start 发 status -> 调用 -> on_tool_end 收集 sources -> LLM 继续流式 token -> done
   c. 需要 calculator -> 静默调用（不发 status）-> on_tool_end 不收集 sources -> LLM 继续流式 token -> done
4. 最多迭代 max_iterations=3 轮工具调用
```

关键决策：

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 工具结果展示 | LLM 整合后统一输出 | 不直接展示原始工具返回，LLM 消化后用自然语言回答 |
| sources 收集 | web_search 结果收集，calculator 不收集 | web_search 有 URL+title（出处语义）；calculator 返回数值，无"来源"概念 |
| status 消息 | web_search 发 status，calculator 不发 | web_search 有网络延迟（用户可感知）；calculator 毫秒级（闪烁像 bug） |
| 搜索关键词 | status 里展示 | "正在搜索：Transformer 原理"，让用户知道 AI 在搜什么 |
| 迭代上限 | max_iterations=3 | 防止 LLM 无限调用工具 |
| 事件流 API | astream_events(version="v2") | LangChain 推荐，可监听 on_tool_start / on_tool_end / on_chat_model_stream |

设计原则（status 按工具类型区分）：
```
只对用户能感知到延迟的工具发 status。
毫秒级工具（calculator 等）静默执行。
未来扩展新工具时，按"用户感知延迟"分类决定是否发 status。
```

FastAPI 端伪代码：
```python
from langchain.agents import AgentExecutor, create_tool_calling_agent

# create_tool_calling_agent 需要 llm + tools + prompt 三参数
agent = create_tool_calling_agent(
    llm=llm,
    tools=[web_search, calculator],
    prompt=prompt,           # ChatPromptTemplate，含 agent_scratchpad 占位符
)

# AgentExecutor 控制 max_iterations
# 注意：stream_events 不是构造函数参数，通过 astream_events() 方法启用
agent_executor = AgentExecutor(
    agent=agent,
    tools=[web_search, calculator],
    max_iterations=3,
)

async def normal_mode_with_tools(agent_executor, prompt, context_sources):
    """normal 模式带工具的流式处理"""
    sources = list(context_sources)  # RAG 检索来源（文档/笔记）

    async for event in agent_executor.astream_events(
        {"input": prompt},
        version="v2",
    ):
        etype = event["event"]

        # --- 工具开始 ---
        if etype == "on_tool_start":
            if event["name"] == "web_search":
                query = event["data"].get("input", {}).get("query", "")
                yield {"event": "status", "data": {"content": f"正在搜索：{query}"}}
            # calculator: 不发 status（毫秒级，闪烁像 bug）

        # --- 工具结束 ---
        elif etype == "on_tool_end":
            if event["name"] == "web_search":
                # 只收集 web_search 的来源（URL + title）
                sources.extend(extract_urls_and_titles(event["data"]["output"]))
            # calculator: 结果丢回 LLM 上下文，不收集 sources

        # --- LLM 流式 token ---
        elif etype == "on_chat_model_stream":
            token = event["data"]["chunk"].content
            if token:
                yield {"event": "token", "data": {"content": token}}

    # 统一 sources（RAG 来源 + web_search 来源合并）
    yield {"event": "sources", "data": {"content": sources}}
    yield {"event": "done", "data": {}}
```

### 8.6 决策点状态

- ~~**决策点 C**：deep 模式 LangGraph 三节点状态变更怎么通过 SSE 推送~~ ✅ 已确认（见 6.6 节）
- ~~**决策点 D**：错误处理 & 连接管理~~ ✅ 已确认（见 8.7 节）

### 8.7 决策点 D：错误处理 & 连接管理（已确认）

#### D.1 LLM 调用失败

统一策略：不重试，快速失败。

| 场景 | 触发条件 | 服务端行为 | SSE error 事件 |
|------|---------|-----------|---------------|
| A | 流式未开始，LLM 连接失败（网络抖动、DNS 失败等） | 日志记详细错误；不写 assistant 到 MySQL；调用 cleanup_failed_turn | "AI 服务暂时不可用，请稍后重试" |
| B | 流式中途中断（API 断开、idle timeout） | 日志记详细错误（含已生成 token 数）；不写 assistant 到 MySQL；调用 cleanup_failed_turn | "回答生成中断，请重新提问" |
| C | DeepSeek 返回非 200（401 key 失效 / 429 限流 / 500 等） | 日志记状态码 + 原始错误；不暴露技术细节给用户；调用 cleanup_failed_turn | "AI 服务暂时不可用，请稍后重试" |

不重试的理由（trade-off）：
- 场景 B 流式中途重试体验极差（已吐一半字突然重来）
- 场景 A 连接阶段重试虽然可行（网络抖动一次重试大概率成功），但需要区分"网络错误可重试 / API 错误不重试"，错误分类逻辑增加代码复杂度，ROI 不够
- 统一不重试是工程上的简化选择

面试话术：*"所有 LLM 调用失败均快速失败不重试。流式输出中途重试体验极差，连接阶段重试虽然可行但需要错误分类逻辑，ROI 不够。生产环境可以考虑对连接阶段的网络错误加一次静默重试。"*

#### D.2 客户端断开连接

感知方式：FastAPI 的 async generator 在客户端断开时收到 `asyncio.CancelledError`（sse-starlette 内部检测连接关闭后取消 generator）。⚠️ 具体行为实现时验证。

CancelledError 的 catch 位置：包裹从检索到 LLM 生成的整段，确保任何位置断开都能走到同一个清理逻辑。

```python
async def chat_stream():
    # 步骤 1：写 MySQL user 消息
    result = db.execute("INSERT INTO chat_history ...", ...)
    user_msg_id = result.lastrowid   # 必须保存，cleanup 要用

    try:
        # 步骤 2~6：检索 + LLM 生成 + SSE 推送
        ...
    except asyncio.CancelledError:
        logger.info(f"客户端断开，session={session_id}")
        await cleanup_failed_turn(redis, db, user_id, session_id, user_msg_id)
        raise  # 重新抛出，不能吞掉，否则 asyncio 认为任务没被正确取消
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}", ...)
        yield {"event": "error", "data": json.dumps({"content": error_message})}
        await cleanup_failed_turn(redis, db, user_id, session_id, user_msg_id)
```

数据处理：

| 数据 | 处理 | 理由 |
|------|------|------|
| MySQL user 消息 | cleanup 时 DELETE | 没有对应 assistant 回复，保留会污染上下文 |
| MySQL assistant 消息 | 不写 | 不完整的回答没有语义价值 |
| Redis user 消息（已 RPUSH） | cleanup 时 RPOP | 和 MySQL 保持一致 |
| Redis round_count（已 +1） | cleanup 时 DECR | 和实际消息数保持一致 |

LLM 生成主动取消：CancelledError 传播到 `llm.astream()` 的迭代，自动中断，省 token。

#### D.3 数据一致性：orphaned user message 处理

问题：LLM 失败或客户端断开后，MySQL/Redis 里有 user 消息但没有对应 assistant 回复（orphaned user message），导致：
- 短期记忆加载时 LLM 看到一条没有回复的 user 消息
- round_count 和实际消息数不一致

方案 A（已选定）：删除 orphaned user message

- error/CancelledError 清理逻辑里，删除最后一条 user 消息（MySQL DELETE + Redis RPOP）
- round_count 回退（DECR）
- 最干净，上下文一致性 100%，代码最简单
- 代价：用户的一次失败提问丢失（影响很小）

否决方案：
- 方案 B（保留，加载时过滤）：加载逻辑复杂，round_count 不可靠
- 方案 C（写标记消息）：脏数据混入 chat_history，LLM 可能尝试回应"[本次回答生成失败]"

cleanup 函数集中处理：

```python
async def cleanup_failed_turn(redis, db, user_id, session_id, user_msg_id):
    """失败后统一清理：best-effort，失败不抛异常

    适用于所有错误路径：场景 A/B/C + 客户端断开。
    三个 try 各自独立——Redis 清理失败不影响 MySQL 清理，反之亦然。
    best-effort 的原因：即使清理失败，Redis 有 TTL 兜底（30 分钟自动消失），
    MySQL 偶尔留一条 orphaned user message 是可接受的极端边界。
    """
    try:
        redis.rpop(f"qa:{user_id}:session:{session_id}:messages")
    except Exception:
        pass
    try:
        redis.decr(f"qa:{user_id}:session:{session_id}:round_count")
    except Exception:
        pass
    try:
        db.execute("DELETE FROM chat_history WHERE id = %s", user_msg_id)
    except Exception:
        pass
```

⚠️ 步骤 1 的 MySQL INSERT 必须保存 `result.lastrowid` 作为 `user_msg_id`，否则 cleanup 拿不到删除目标。

#### D.4 超时策略

| 层面 | 决定 | 实现方式 |
|------|------|---------|
| 整个请求 | 不设总超时 | SSE 靠 done/error 事件结束；客户端断开靠 CancelledError |
| 检索阶段 | 不设独立超时 | Chroma / MySQL / Redis 均为本地操作，毫秒级 |
| Agent A LLM（deep 模式） | 30 秒总超时 | ChatOpenAI timeout 参数（具体参数名实现时查文档确认） |
| Agent C LLM（deep 模式） | 30 秒 idle timeout | stream_with_idle_timeout 包 __anext__() |
| normal 模式 LLM | 30 秒 idle timeout | 同理，作用在 LLM token 间隔上 |

idle timeout 实现：包 `__anext__()` 不是包整个迭代器（否则变成总超时）：

```python
async def stream_with_idle_timeout(aiter, timeout=30):
    """每个 chunk 之间设 idle 超时，不是总超时"""
    while True:
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout)
            yield chunk
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise TimeoutError(f"LLM 无响应超过 {timeout} 秒")
```

30 秒阈值依据：DeepSeek 正常 TTTV（首 token 延迟）1~3 秒，token 间隔 0.03~0.1 秒。30 秒无 token 必为异常。

normal 模式不误触发：工具执行（web_search 3~5 秒）发生在两次独立 LLM 调用之间，不在同一次 astream() 内，idle timeout 天然不覆盖。

Agent B 不调 LLM（只做 Chroma 检索），不需要超时。

#### D.5 SSE error 事件粒度

统一模糊，不暴露技术细节。三种用户可见消息：

| 消息 | 适用场景 | 行动建议 |
|------|---------|---------|
| "AI 服务暂时不可用，请稍后重试" | 场景 A（生成前失败）、场景 C（HTTP 错误） | 等服务恢复后再试 |
| "回答生成中断，请重新提问" | 场景 B（生成中断、idle timeout） | 可以立即重试 |
| "请求超时，请重新提问" | Agent A 超时（deep 模式） | 可以立即重试 |

不区分 429/401/500 等具体错误码——对用户来说行动完全一样（等一下再试），区分反而增加认知负担。

服务端日志记完整信息：
- 错误类型（ConnectionError / HTTPError / TimeoutError）
- HTTP 状态码（如果有）
- DeepSeek 返回的原始错误消息
- user_id / session_id / 时间戳
- 已生成的 token 数（场景 B）

Gradio 翻译层处理：error 事件清空 status 栏，避免"正在生成回答..."和错误提示自相矛盾：

```python
elif event["type"] == "error":
    status_component.update("")  # 清空状态栏
    yield accumulated + f"\n\n⚠️ {event['content']}"  # 错误提示拼入消息末尾
    return
```

---

## 模块九：Gradio 界面（已确认）

Gradio 是薄前端，消费 FastAPI SSE 端点，不做业务逻辑。翻译层设计见模块八 8.4 节。

### 9.1 整体布局

用 Blocks，不用 ChatInterface。

理由：ChatInterface 只能放聊天组件，无法加文件上传、模式切换、状态栏等自定义 UI。Blocks 可以自由拼装组件。

两个 Tab：

```
Tab 1: 💬 对话（主页面）
  ┌──────────────────────┬──────────────────┐
  │ 左侧：聊天区          │ 右侧：文档区      │
  │  gr.Radio（模式切换）  │  gr.File（上传）   │
  │  gr.Markdown（状态栏） │  gr.Dataframe     │
  │  gr.Chatbot（聊天记录）│  gr.Dropdown      │
  │  gr.Textbox（输入框）  │  gr.Button（删除） │
  └──────────────────────┴──────────────────┘

Tab 2: 📝 笔记
  ┌────────────────────────────────────────┐
  │ gr.Dataframe（笔记列表）                 │
  │ gr.Dropdown + [编辑] [删除]              │
  │ ────────────────────────────────────     │
  │ 编辑区                         [新建笔记] │
  │ 概念：gr.Textbox                        │
  │ 内容：gr.Textbox (multiline)            │
  │                             [保存笔记]   │
  └────────────────────────────────────────┘
```

文档放对话 Tab（不放独立 Tab）：上传到提问是连续动作，拆开增加切换成本。

### 9.2 模式切换

组件：gr.Radio（单选按钮），不用 Dropdown。

理由：只有两个选项，Radio 一眼可见全部选项且时刻可见当前模式。Dropdown 适合选项多的场景。

位置：输入框上方。

默认值：普通问答（normal）。normal 快（2~5 秒），deep 慢（10~18 秒），大多数问题 normal 够用。

标签加预期提示：
```
○ 普通问答   ○ 深度研究（较慢，10~18 秏）
```

切换不重置 session：两个模式共用同一个 session 的上下文，模式切换只影响下一次请求的 mode 参数。

### 9.3 文档上传交互

上传组件：gr.File，支持 .pdf / .docx / .md / .txt / .html / .htm。

处理方式：同步等待。FastAPI POST /api/v1/documents 处理完以下步骤才返回：
1. 读取文件原始内容 → `hashlib.sha256(content).hexdigest()` → content_hash
2. 加载文档内容（loader）
3. 切片（splitter）
4. 向量化写入 ChromaDB
5. MySQL 记录元数据（含 content_hash，UNIQUE INDEX idx_doc_hash 去重）

60 秒超时保护（在 Gradio httpx 端设置 `timeout=httpx.Timeout(60.0)`）：大文件（100 页 PDF）embedding 可能耗时 15~30 秒，加加载切片可能接近上限。超时后 Gradio 前端提示"上传超时，请重试"。

documents 表 status 三态管理：

```
INSERT 时：status='processing'    ← 上传开始，开始处理
处理成功：status='ready'          ← 可用于问答
处理失败/超时：status='failed'    ← 异常
文档列表只展示 status='ready'
```

超时/失败清理：Chroma 按 metadata.mysql_id 定位删除已写入的部分向量。

failed 状态的用户反馈：在状态栏显示提示"⚠️ 文档《xxx.pdf》处理失败，请重试"。failed 文档保留在 MySQL（排查用），用户列表不可见。

文档列表展示：gr.Dataframe（只读）

| 字段 | 来源 |
|------|------|
| 文件名 | documents.filename |
| 切片数 | documents.chunk_count |
| 上传时间 | documents.created_at |

⚠️ gr.Dataframe 是纯展示组件，不支持行内按钮。

删除交互：gr.Dropdown（选择文档）+ gr.Button（删除）。两步操作提供隐式确认屏障，直接删除不再单独弹确认。删除时同步清理 MySQL + Chroma + 本地文件（best-effort：MySQL 删成功但 Chroma/文件删除失败时，孤儿向量只占空间不影响正确性，可接受）。

三状态反馈：

| 状态 | 来源 | 说明 |
|------|------|------|
| 上传中 | gr.File 内置进度条 | 不需要自定义 |
| 处理中 | gr.Markdown 显示"正在处理文档..." | 等待 FastAPI 返回 |
| 完成 | 刷新 Dataframe 列表 + "文档《xxx》已就绪，可以开始提问" | 或 failed 提示 |

### 9.4 笔记功能

笔记列表：gr.Dataframe（只读）

| 字段 | 来源 |
|------|------|
| 概念 | semantic_memory.concept |
| 创建时间 | semantic_memory.created_at |
| 内容预览 | semantic_memory.content 截取前 50 字 |

选择/操作：gr.Dropdown（选择笔记）+ gr.Button（编辑）+ gr.Button（删除）。和文档管理一致的交互模式。删除直接执行不确认。

编辑区：

```
├──────────────────────────────────────────┤
│ 编辑区                     [新建笔记]     │  ← 清空编辑区 + note_id = None
│ 概念：[________________________]          │  ← gr.Textbox
│ 内容：[________________________]          │  ← gr.Textbox (multiline)
│                            [保存笔记]     │  ← gr.Button
└──────────────────────────────────────────┘
```

新建/编辑共用编辑区，用 gr.State(note_id) 区分 POST/PUT：

| 场景 | 操作 | API |
|------|------|-----|
| 新建笔记 | 点"新建笔记"→ 清空编辑区 + note_id=None → 填写 → 点"保存笔记" | POST /api/v1/notes |
| 编辑笔记 | Dropdown 选笔记 → 点"编辑"→ 编辑区填充 + note_id=选中ID → 修改 → 点"保存笔记" | PUT /api/v1/notes/{id} |
| 删除笔记 | Dropdown 选笔记 → 点"删除" | DELETE /api/v1/notes/{id} |

"新建笔记"按钮是必须的：没有它，用户编辑笔记后（note_id 已设值）直接改内容点保存，会走 PUT 覆盖已有笔记。note_id 是 UI 不可见状态，必须有对应的可见操作入口重置。

### 9.5 状态栏

组件：gr.Markdown，独立于聊天消息。位置在 Radio 和 Chatbot 之间：

```
┌─────────────────────────────┐
│  ○ 普通问答   ○ 深度研究      │  ← gr.Radio
├─────────────────────────────┤
│  （status 栏，通常为空）      │  ← gr.Markdown
├─────────────────────────────┤
│  [聊天记录区域]               │  ← gr.Chatbot
├─────────────────────────────┤
│  [输入框]            [发送]   │  ← gr.Textbox + Button
└─────────────────────────────┘
```

两种模式下的 status 栏行为：

| 场景 | status 栏内容 | 时长 |
|------|-------------|------|
| normal 模式，不调工具 | 空 | — |
| normal 模式，调 web_search | "正在搜索：Transformer 原理" | 3~5 秒 |
| normal 模式，调 calculator | 空（毫秒级，不发 status） | — |
| deep 模式 | 6 条 status 依次更新（见 6.6 节） | 10~18 秒 |

更新/清空时机：

| 事件 | status 栏行为 |
|------|-------------|
| status 事件到达 | 更新为新的 status 内容 |
| 第一个 token 到达 | 清空 |
| done 事件 | 清空 |
| error 事件 | 清空（见 D.5） |

第一个 token 清空 status 的实现（Gradio yield dict 只更新显式指定的组件）：

```python
# chatbot_history 是完整的消息列表（history + 当前 assistant 消息），见 8.4 节
if first_token:
    yield {status_md: "", chatbot: chatbot_history}  # 同时清空 status + 更新聊天
    first_token = False
else:
    yield {chatbot: chatbot_history}  # 只更新聊天，status 已空不动
```

后续 token 不带 status_md，不影响已清空的状态栏。

---

## 模块十：项目目录结构（已确认）

### 10.1 完整目录结构

```
ai-knowledge-qa/
├── app/                      # FastAPI 应用
│   ├── main.py              # FastAPI 入口，路由注册
│   ├── api/                 # API 路由
│   │   ├── routes_chat.py   # /api/v1/chat 端点
│   │   ├── routes_documents.py
│   │   ├── routes_notes.py
│   │   ├── routes_sessions.py
│   │   └── routes_users.py
│   ├── models/              # Pydantic 请求/响应模型
│   │   └── schemas.py
│   ├── deps.py              # 依赖注入（DB session、Redis 连接等）
│   └── error_handler.py     # 统一错误处理
│
├── rag/                      # RAG 系统（模块一）
│   ├── loader.py            # 文档加载
│   ├── splitter.py          # 文档分块
│   ├── vector_store.py      # 向量化和存储
│   ├── retriever.py         # 检索组件
│   └── llm.py               # LLM 配置
│
├── memory/                   # 记忆系统（模块二）
│   ├── short_term.py        # 短期记忆（Redis）
│   ├── semantic.py          # 语义记忆
│   ├── episodic.py          # 情景记忆
│   └── retrieve.py          # 并行检索
│
├── agents/                   # 多 Agent（模块六）
│   ├── graph.py             # LangGraph 工作流定义
│   ├── state.py             # ResearchState 定义
│   ├── decomposer.py        # Agent A
│   ├── doc_searcher.py      # Agent B（文档检索，避免和 rag/retriever.py 同名）
│   └── summarizer.py        # Agent C
│
├── tools/                    # Tool Use（模块七）
│   ├── web_search.py
│   └── calculator.py
│
├── frontend/                 # Gradio 界面（模块九）
│   └── app.py
│
├── db/                       # 数据库
│   ├── database.py          # SQLAlchemy 引擎 + Session
│   ├── models.py            # ORM 模型定义
│   └── init_db.py           # 建表脚本
│
├── tests/                    # 测试
│   ├── test_rag.py           # loader / splitter / vector_store
│   ├── test_memory.py        # short_term / semantic / episodic
│   ├── test_api.py           # FastAPI 端点
│   └── test_agents.py        # LangGraph 工作流
│
├── data/                     # 运行时数据（gitignore）
│   ├── uploads/             # 用户上传的文档（UUID 重命名）
│   └── chroma_db/           # ChromaDB 持久化存储
│
├── config.py                 # 配置读取（从 .env）
├── .env                      # 环境变量（gitignore）
├── .env.example              # 环境变量示例（提交到 git）
│   # .env.example 内容就是把 .env 的敏感值替换为占位符：
│   # DEEPSEEK_API_KEY=sk-your-key-here
│   # MYSQL_PASSWORD=your-password-here
│   # 其他有默认值或无敏感信息的项原样保留
├── .gitignore
├── requirements.txt
└── README.md
```

设计选择：
- **按功能模块组织**，和计划里的模块划分对齐
- **db/ 独立**，不放在 app/ 里——ORM 模型被 app/、memory/、agents/ 多个模块共用，放 app/ 里会产生循环依赖方向问题
- **config.py 在顶层**，所有模块都读配置，导入路径最短
- **data/ 全部 gitignore**，运行时产生的数据不提交
- **tests/ 预留**，实现时每个模块的验证有地方放

### 10.2 包依赖关系

导入方式：**绝对导入**，不用相对导入。

```python
from rag.loader import load_document
from memory.short_term import get_short_term_memory
from db.models import ChatHistory
```

依赖方向单向，无循环：
```
app/ → rag/ / memory/ / agents/ / tools/ → db/ / config.py
frontend/ → 不 import 后端任何包
```

**关键约束：frontend/ 不 import 后端任何包（app/ rag/ memory/ agents/ tools/ db/ config.py）。前后端之间只有 HTTP 通信，没有 import 依赖。**

### 10.3 运行方式

两个进程独立运行，从项目根目录执行：

```
后端：uvicorn app.main:app --reload --port 8000
前端：python frontend/app.py
```

Gradio 前端通过 HTTP（httpx.AsyncClient）调 FastAPI 后端。

### 10.4 配置管理

**.env 内容：**

```env
# LLM
DEEPSEEK_API_KEY=sk-xxx

# 工具
TAVILY_API_KEY=tvly-xxx

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=xxx
MYSQL_DATABASE=ai_qa

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=               # 无密码留空，生产环境建议设密码

# 前端（Gradio 用，不 import backend config）
API_URL=http://localhost:8000

# 路径（有默认值，可选）
CHROMA_PERSIST_DIR=./data/chroma_db
UPLOAD_DIR=./data/uploads
```

**config.py（后端用）：**

用 python-dotenv + os.getenv，不用 pydantic-settings（项目配置项不多，少一个依赖）。

```python
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()

# 必填校验：放在 URL 构建之前，否则 quote_plus(None) 会抛 TypeError 而不是友好的 RuntimeError
_REQUIRED = ["DEEPSEEK_API_KEY", "TAVILY_API_KEY", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
for key in _REQUIRED:
    if not os.getenv(key):
        raise RuntimeError(f"环境变量 {key} 未设置，请检查 .env 文件")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# MySQL（带默认值，密码用 quote_plus 编码，防止特殊字符破坏 URL）
_mysql_host = os.getenv("MYSQL_HOST", "localhost")
_mysql_port = os.getenv("MYSQL_PORT", "3306")
MYSQL_URL = f"mysql+pymysql://{os.getenv('MYSQL_USER')}:{quote_plus(os.getenv('MYSQL_PASSWORD'))}@{_mysql_host}:{_mysql_port}/{os.getenv('MYSQL_DATABASE')}"

# Redis（支持密码，带默认值，密码同样用 quote_plus 编码，和 MySQL 对称）
_redis_host = os.getenv("REDIS_HOST", "localhost")
_redis_port = os.getenv("REDIS_PORT", "6379")
_redis_db = os.getenv("REDIS_DB", "0")
_redis_password = os.getenv("REDIS_PASSWORD", "")
_redis_auth = f":{quote_plus(_redis_password)}@" if _redis_password else ""
REDIS_URL = f"redis://{_redis_auth}{_redis_host}:{_redis_port}/{_redis_db}"
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/uploads")
```

**前端侧（frontend/app.py）独立读 API_URL：**

```python
import os
API_URL = os.getenv("API_URL", "http://localhost:8000")
```

### 10.5 依赖管理

**requirements.txt（分组）：**

```
# Web 框架
fastapi
uvicorn
sse-starlette
python-multipart          # 文件上传必需（UploadFile 依赖）
gradio>=4.0,<6.0           # 锁版本：Blocks API 变动频繁，5.x 已稳定可用
httpx

# LLM / Agent
langchain>=0.3,<0.4       # 锁版本：迭代快，API 变动频繁
langchain-openai
langchain-community
langgraph>=0.2,<0.3       # 锁版本：astream_events / get_stream_writer 是较新 API

# RAG
chromadb>=0.5,<0.6         # 锁版本：embedding/metadata API 变动频繁
langchain-text-splitters
pymupdf                   # PyMuPDFLoader 底层依赖（不是 pymupdf4llm）
docx2txt
unstructured
beautifulsoup4

# Embedding
sentence-transformers

# 数据库
sqlalchemy
pymysql
redis

# 工具
python-dotenv
tavily-python
numexpr                    # calculator 工具的安全数学运算

# 测试
pytest
```

⚠️ HuggingFaceEmbeddings 导入路径需要实现时验证：
```python
# 如果旧路径报错，需要 pip install langchain-huggingface，用新路径
from langchain_community.embeddings import HuggingFaceEmbeddings  # 旧路径
# 或
from langchain_huggingface import HuggingFaceEmbeddings            # 新路径
```

跑通后存完整快照：
```
pip freeze > requirements.lock
```

### 10.6 文件存储

上传文件用 **UUID 重命名**：`data/uploads/{uuid}.{ext}`

三层职责清晰：

| 层 | 存什么 | 职责 |
|----|--------|------|
| 文件系统 | {uuid}.{ext} | 不冲突、无特殊字符问题 |
| documents.filename | 原始文件名 | 给用户看 |
| documents.file_path | UUID 路径 | 系统访问文件 |
| documents.content_hash | 内容哈希 | DB 层去重（UNIQUE INDEX idx_doc_hash） |

### 10.7 ChromaDB 持久化

路径：`data/chroma_db/`（通过 CHROMA_PERSIST_DIR 配置）。

两个 collection：
- `rag_documents` — RAG 文档向量（metadata where={"user_id": xxx} 隔离用户）
- `semantic_memory` — 语义记忆向量（metadata where={"user_id": xxx} 隔离用户）

---

## 待确认模块（后续补充）

以下模块尚未确认细节，后续逐步讨论：

- **模块十一：学习报告 / 统计** — 报告内容、统计指标（P2）
