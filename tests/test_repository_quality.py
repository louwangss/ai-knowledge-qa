"""CI、依赖声明与作品集文档的静态质量门禁。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_ci_runs_without_repository_secrets():
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

    assert "actions/checkout@v6" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "python -m pip check" in workflow
    assert "python -m pytest -q" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "${{ secrets." not in workflow


def test_requirements_declares_direct_langchain_integrations():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    for package in ("langchain-chroma", "langchain-huggingface", "langchain-classic"):
        assert package in requirements


def test_readme_documents_security_and_evaluation_limits():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Bearer" in readme
    assert "Gradio 本身没有独立登录" in readme
    assert "deterministic-char-bigram-v1" in readme
    assert "不代表生产数据分布" in readme
    assert "C:\\Users\\" not in readme
