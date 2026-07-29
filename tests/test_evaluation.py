"""离线 RAG 评测的数据、指标与 CLI 回归测试。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation.run_eval import (
    DatasetError,
    DeterministicCharNgramRetriever,
    evaluate,
    extract_citations,
    load_dataset,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_PATH = PROJECT_ROOT / "evaluation" / "dataset.json"


def test_public_dataset_has_valid_source_references():
    dataset = load_dataset(DATASET_PATH)

    assert dataset["dataset_id"] == "aiqa-public-mini-v1"
    assert len(dataset["documents"]) >= 4
    assert len(dataset["cases"]) >= 4


def test_dataset_rejects_unknown_expected_source(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(
            {
                "dataset_id": "invalid",
                "version": "1",
                "documents": [{"source": "known.md", "content": "内容"}],
                "cases": [
                    {
                        "id": "q1",
                        "question": "问题",
                        "expected_sources": ["unknown.md"],
                        "reference_answer": "回答",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetError, match="unknown.md"):
        load_dataset(path)


def test_deterministic_retriever_ranks_relevant_document_first():
    retriever = DeterministicCharNgramRetriever(
        [
            {"source": "rag.md", "content": "检索增强生成先检索知识库文档再生成回答"},
            {"source": "redis.md", "content": "Redis 保存带过期时间的 Web 登录会话"},
        ]
    )

    results = retriever.search("检索增强生成怎样使用知识库", top_k=1)

    assert results[0]["source"] == "rag.md"
    assert 0 < results[0]["score"] <= 1


def test_evaluation_reports_retrieval_and_citation_metrics():
    dataset = load_dataset(DATASET_PATH)

    result = evaluate(dataset, top_k=2, evaluated_at="2026-07-27T00:00:00Z")

    assert result["schema_version"] == 1
    assert result["dataset"]["case_count"] == len(dataset["cases"])
    assert result["retriever"]["version"] == "deterministic-char-bigram-v1"
    assert result["metrics"]["recall_at_k"] == 1.0
    assert result["metrics"]["source_coverage_at_k"] == 1.0
    assert result["metrics"]["citation_completeness"] == 1.0
    assert all(case["retrieval_hit"] for case in result["cases"])


def test_extract_citations_deduplicates_source_names():
    answer = "根据《rag.md》可知。再次参考《rag.md》，并结合《memory.md》。"

    assert extract_citations(answer) == ["rag.md", "memory.md"]


def test_cli_writes_structured_result(tmp_path):
    output = tmp_path / "result.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "evaluation" / "run_eval.py"),
            "--dataset",
            str(DATASET_PATH),
            "--top-k",
            "2",
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["metrics"]["recall_at_k"] == 1.0
    assert payload["dataset"]["sha256"]
