"""CI、依赖声明与作品集文档的静态质量门禁。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_ci_runs_without_repository_secrets():
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v6" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "python -m pip check" in workflow
    assert "python -m pytest -q" in workflow
    assert "python -m db.init_db --upgrade" in workflow
    assert "mysql:8.4" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "${{ secrets." not in workflow


def test_requirements_declares_direct_langchain_integrations():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    for package in ("langchain-core", "langchain-chroma", "langchain-huggingface"):
        assert package in requirements

    for retired in ("langchain-classic", "sse-starlette", "beautifulsoup4", "numexpr"):
        assert retired not in requirements.lower()


def test_private_context_is_not_exposed_to_tool_calling_agent():
    chat_route = (PROJECT_ROOT / "app" / "api" / "routes_chat.py").read_text(encoding="utf-8")

    assert "create_agent" not in chat_route
    assert "literal_eval" not in chat_route
    assert not (PROJECT_ROOT / "tools" / "web_search.py").exists()
    assert "plan_web_search" in chat_route
    assert "payload.message, current_date" in chat_route


def test_collection_endpoints_expose_bounded_pagination():
    from app.main import app

    paths = app.openapi()["paths"]
    for path, method in (
        ("/api/v1/sessions", "get"),
        ("/api/v1/documents", "get"),
        ("/api/v1/notes", "get"),
        ("/api/v1/notes/summaries", "get"),
        ("/api/v1/chat/history", "get"),
    ):
        parameters = {
            item["name"]: item for item in paths[path][method]["parameters"]
        }
        assert parameters["limit"]["schema"]["maximum"] == 100
        assert parameters["limit"]["schema"]["default"] == 100
        assert parameters["offset"]["schema"]["minimum"] == 0


def test_gradio_runtime_is_fully_retired():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "gradio" not in requirements
    assert not (PROJECT_ROOT / "frontend" / "app.py").exists()
    assert "Gradio" not in readme

    from app.main import app

    paths = app.openapi()["paths"]
    assert "/api/v1/bootstrap" not in paths
    assert "/api/v1/users" not in paths


def test_legacy_redis_chat_memory_is_fully_retired():
    """旧实现不得重新引入，Redis 不能再次成为对话事实来源。"""
    assert not (PROJECT_ROOT / "memory" / "short_term.py").exists()

    production_roots = ["app", "agents", "memory", "rag", "tools"]
    legacy_imports = []
    for root_name in production_roots:
        for path in (PROJECT_ROOT / root_name).rglob("*.py"):
            if "memory.short_term" in path.read_text(encoding="utf-8"):
                legacy_imports.append(path.relative_to(PROJECT_ROOT).as_posix())

    assert legacy_imports == []


def test_readme_documents_security_and_evaluation_limits():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Bearer" in readme
    assert "HttpOnly" in readme
    assert "完整多租户认证" in readme
    assert "deterministic-char-bigram-v1" in readme
    assert "不代表生产数据分布" in readme
    assert "C:\\Users\\" not in readme
