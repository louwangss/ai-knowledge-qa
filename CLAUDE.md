# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI 知识库问答系统，基于 RAG + 多 Agent 架构。用户上传文档后，系统通过向量检索 + LLM 回答问题；支持普通问答和深度研究（多 Agent 拆解-检索-整合）两种模式。

技术栈：Python / FastAPI / MySQL / Redis / LangChain / LangGraph / ChromaDB / Gradio / DeepSeek。

## 常用命令

### 环境准备
```bash
# 激活虚拟环境（Windows bash）
source venv/Scripts/activate
# 安装依赖
pip install -r requirements.txt
# 复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY、MySQL、Redis 凭据
```

### 数据库初始化（首次运行必做）
```bash
python -m db.init_db            # 仅建表 + 索引
python -m db.init_db --drop     # 先删再建（会清空所有数据）
```

### 启动服务
```bash
# 后端 API（端口 8000）
uvicorn app.main:app --reload

# 前端 UI（端口 7860，需先启动后端）
python -m frontend.app
```

### 测试
```bash
pytest                          # 运行全部测试
pytest tests/path_to_test.py    # 运行单个测试文件
pytest tests/path_to_test.py::test_func  # 运行单个测试函数
```

## 架构与数据流

### 分层结构
- `app/`：FastAPI 路由层（`api/` 下按资源拆分 routes_*.py）、Pydantic schema、依赖注入、错误处理。入口在 `app/main.py`，启动时注册 5 个 router + 后台补偿任务。
- `agents/`：LangGraph 多 Agent 深度研究工作流（见下）。
- `rag/`：RAG 组件（loader / splitter / vector_store / retriever / llm）。
- `memory/`：三层记忆系统（见下）。
- `db/`：SQLAlchemy ORM 模型 + 引擎 + 建表脚本。
- `tools/`：LangChain `@tool` 装饰的工具（web_search / calculator），供 normal 模式 AgentExecutor 调用。
- `frontend/`：Gradio 薄 UI 层，通过 httpx 调 FastAPI SSE。
- `config.py`：单点读取 `.env`，必填校验在导入时即触发。

### Chat 主链路（`app/api/routes_chat.py`）
POST `/api/v1/chat`，SSE 流式输出，按顺序：
1. MySQL 写 user 消息 → 2-4. Redis 追加消息 + 续期 → 5. 并行检索上下文（`memory/retrieve.py`）→ 6. LLM 流式生成 → 7. MySQL 写 assistant 消息 → 8. Redis 追加 + 摘要压缩判断 → 9. 写 episodic 记忆 → 10-11. 更新 last_active + 续期。
失败时 `_cleanup` 回滚 Redis 与 MySQL user 消息。

### 双模式
- **normal**：单次 LLM 调用，prompt 注入检索到的文档/笔记/记忆。若 `TAVILY_API_KEY` 已配置，自动升级为 `AgentExecutor`（工具：web_search、calculator，max_iterations=3），失败 fallback 到普通流式。
- **deep**：调用 `agents/graph.py` 的 LangGraph，三节点线性工作流：Agent A（`decomposer`，拆解 3-5 子问题）→ Agent B（`doc_searcher`，对每个子问题并行检索 Chroma 并按 content 去重）→ Agent C（`summarizer`，`get_stream_writer` 逐 token 输出）。deep 模式下 `retrieve_context` 跳过文档检索（交给 Agent B）。

### 三层记忆系统
- **短期记忆**（`memory/short_term.py`，Redis）：4 个 key（summary、messages、round_count、next_compress_round），TTL 30 分钟。到第 11 轮首次压缩，之后每 5 轮一次；每次 LLM 合并最旧 10 条消息到 summary 后 ltrim 截断。Redis miss 时从 MySQL 加载最近 10 条恢复。
- **情景记忆**（`memory/episodic.py`，MySQL `episodic_memory` 表）：记录关键事件（qa_completed、document_loaded、note_saved），30 秒去重窗口。
- **语义记忆**（`memory/semantic.py`，MySQL `semantic_memory` + Chroma `semantic_memory` collection）：用户笔记，写时做 0.9 相似度去重。MySQL 为主、Chroma 为索引，chroma_id 异步同步；`app/main.py` 后台任务每 5 分钟补偿 `chroma_id IS NULL` 的记录。

### 向量库设计
- ChromaDB 持久化在 `./data/chroma_db`，**双 collection**：`rag_documents`（文档分块）和 `semantic_memory`（笔记）。
- **用户隔离靠 metadata filter**（`{"user_id": ..., "type": "document|note"}`），非物理分库。
- 笔记 metadata 中 `mysql_id` 为字符串；deep 模式 Agent B 文档检索同理。
- Embedding 模型：`BAAI/bge-small-zh-v1.5`（HuggingFaceEmbeddings 单例，normalize_embeddings=True）。

### ORM 关键表
`users`（UUID 主键）、`sessions`、`documents`（含 content_hash 唯一索引去重）、`chat_history`（ BigInteger 自增，role/user/assistant）、`episodic_memory`、`semantic_memory`（chroma_id 可空）。索引在 `db/init_db.py` 中显式创建。

## 约定

- LLM 默认 DeepSeek（`deepseek-chat`），`rag/llm.py::get_llm` 单点封装；`config.py` 中 `LLM_MODEL` / `LLM_BASE_URL` 可覆盖。
- 文档分块固定 `chunk_size=1000, chunk_overlap=200`（`rag/splitter.py` 与 `routes_documents.py` 保持一致）。
- 所有路由前缀 `/api/v1/<resource>`。
- 用户为 MVP 单用户模型：前端 `DEFAULT_USER_ID = "default-user"`，页面加载时自动创建 user + session。
- SSE 事件类型：`token`（增量内容）、`status`（deep 模式阶段提示）、`sources`（引用列表）、`done`、`error`。
- 中文回复默认；代码注释与文档均为中文。
- `TAVILY_API_KEY`、`REDIS_PASSWORD` 等可选项在 `.env` 中留空即禁用对应功能。

## 参考

详细实现思路见仓库根目录 `IMPLEMENTATION_PLAN.md`（中文，含每模块代码草案与设计决策）。
