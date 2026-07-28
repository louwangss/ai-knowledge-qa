"""应用启动链路不应提前加载仅在问答或文档处理时使用的重依赖。"""

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_backend_import_defers_heavy_rag_dependencies():
    heavy_modules = (
        "chromadb",
        "langchain_openai",
        "langchain_community.document_loaders",
        "sentence_transformers",
        "torch",
    )
    script = (
        "import sys; import app.main; "
        f"names={heavy_modules!r}; "
        "print(','.join(name for name in names if name in sys.modules))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == ""
