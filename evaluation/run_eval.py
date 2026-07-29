"""无密钥 RAG 离线评测 CLI。

默认使用确定性的字符 bigram 余弦相似度作为轻量检索基线。它不替代生产 BGE
向量检索，只为公开数据集、指标实现和 CI 提供可重复的工程证据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RETRIEVER_VERSION = "deterministic-char-bigram-v1"
SCHEMA_VERSION = 1
_CITATION_PATTERN = re.compile(r"《([^》]+)》")


class DatasetError(ValueError):
    """评测数据不满足可执行 schema。"""


def _require_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{field} 必须是非空字符串")
    return value.strip()


def load_dataset(path: str | Path) -> dict:
    dataset_path = Path(path)
    try:
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"无法读取评测数据: {dataset_path.name}") from exc

    if not isinstance(dataset, dict):
        raise DatasetError("评测数据根节点必须是对象")
    _require_non_empty_string(dataset.get("dataset_id"), "dataset_id")
    _require_non_empty_string(dataset.get("version"), "version")

    documents = dataset.get("documents")
    cases = dataset.get("cases")
    if not isinstance(documents, list) or not documents:
        raise DatasetError("documents 必须是非空数组")
    if not isinstance(cases, list) or not cases:
        raise DatasetError("cases 必须是非空数组")

    sources = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise DatasetError(f"documents[{index}] 必须是对象")
        sources.append(_require_non_empty_string(document.get("source"), f"documents[{index}].source"))
        _require_non_empty_string(document.get("content"), f"documents[{index}].content")
    if len(set(sources)) != len(sources):
        raise DatasetError("documents.source 不得重复")

    known_sources = set(sources)
    case_ids = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise DatasetError(f"cases[{index}] 必须是对象")
        case_ids.append(_require_non_empty_string(case.get("id"), f"cases[{index}].id"))
        _require_non_empty_string(case.get("question"), f"cases[{index}].question")
        _require_non_empty_string(case.get("reference_answer"), f"cases[{index}].reference_answer")
        expected_sources = case.get("expected_sources")
        if not isinstance(expected_sources, list) or not expected_sources:
            raise DatasetError(f"cases[{index}].expected_sources 必须是非空数组")
        unknown = set(expected_sources) - known_sources
        if unknown:
            raise DatasetError(f"cases[{index}] 引用了未知来源: {', '.join(sorted(unknown))}")
    if len(set(case_ids)) != len(case_ids):
        raise DatasetError("cases.id 不得重复")

    return dataset


def _bigrams(text: str) -> Counter[str]:
    normalized = "".join(char.lower() for char in text if char.isalnum())
    if len(normalized) < 2:
        return Counter([normalized]) if normalized else Counter()
    return Counter(normalized[index:index + 2] for index in range(len(normalized) - 1))


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(value * right.get(token, 0) for token, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


class DeterministicCharNgramRetriever:
    """无需模型下载的字符 bigram 检索基线。"""

    def __init__(self, documents: list[dict]):
        self._documents = [
            {**document, "_vector": _bigrams(document["content"])}
            for document in documents
        ]

    def search(self, question: str, top_k: int) -> list[dict]:
        if top_k < 1:
            raise ValueError("top_k 必须大于 0")
        query_vector = _bigrams(question)
        ranked = [
            {
                "source": document["source"],
                "score": _cosine_similarity(query_vector, document["_vector"]),
            }
            for document in self._documents
        ]
        ranked.sort(key=lambda item: (-item["score"], item["source"]))
        return [
            {**item, "score": round(item["score"], 6)}
            for item in ranked[:top_k]
        ]


def extract_citations(answer: str) -> list[str]:
    return list(dict.fromkeys(_CITATION_PATTERN.findall(answer)))


def _dataset_sha256(dataset: dict) -> str:
    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate(dataset: dict, top_k: int = 2, evaluated_at: str | None = None) -> dict:
    retriever = DeterministicCharNgramRetriever(dataset["documents"])
    case_results = []
    hit_count = 0
    expected_source_count = 0
    retrieved_expected_count = 0
    cited_expected_count = 0

    for case in dataset["cases"]:
        ranked = retriever.search(case["question"], top_k=top_k)
        retrieved_sources = [item["source"] for item in ranked]
        expected_sources = list(case["expected_sources"])
        citations = extract_citations(case["reference_answer"])
        retrieved_expected = set(retrieved_sources) & set(expected_sources)
        cited_expected = set(citations) & set(expected_sources)
        retrieval_hit = bool(retrieved_expected)

        hit_count += int(retrieval_hit)
        expected_source_count += len(expected_sources)
        retrieved_expected_count += len(retrieved_expected)
        cited_expected_count += len(cited_expected)
        case_results.append(
            {
                "id": case["id"],
                "expected_sources": expected_sources,
                "retrieved_sources": retrieved_sources,
                "scores": [item["score"] for item in ranked],
                "retrieval_hit": retrieval_hit,
                "reference_citations": citations,
            }
        )

    case_count = len(dataset["cases"])
    timestamp = evaluated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": timestamp,
        "dataset": {
            "id": dataset["dataset_id"],
            "version": dataset["version"],
            "sha256": _dataset_sha256(dataset),
            "document_count": len(dataset["documents"]),
            "case_count": case_count,
        },
        "retriever": {
            "name": "deterministic_char_bigram_cosine",
            "version": RETRIEVER_VERSION,
            "top_k": top_k,
        },
        "metrics": {
            "recall_at_k": round(hit_count / case_count, 6),
            "source_coverage_at_k": round(retrieved_expected_count / expected_source_count, 6),
            "citation_completeness": round(cited_expected_count / expected_source_count, 6),
        },
        "cases": case_results,
        "limitations": [
            "公开合成小样本仅验证评测链路与轻量检索基线，不代表生产数据分布。",
            "默认检索器不是生产使用的 BGE embedding，不能据此推断线上向量检索效果。",
            "引用完整性针对固定参考答案计算，不评估实时 LLM 生成质量。",
        ],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行无密钥 RAG 离线评测")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("dataset.json"),
        help="评测数据集 JSON 路径",
    )
    parser.add_argument("--top-k", type=int, default=2, help="每题返回的来源数量")
    parser.add_argument("--output", type=Path, help="可选的 JSON 输出路径；默认打印到 stdout")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        dataset = load_dataset(args.dataset)
        result = evaluate(dataset, top_k=args.top_k)
    except (DatasetError, ValueError) as exc:
        raise SystemExit(f"评测失败: {exc}") from exc

    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
