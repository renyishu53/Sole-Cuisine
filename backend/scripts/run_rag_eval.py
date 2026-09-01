"""RAG 检索质量离线评测脚本（CI 可重复运行）。

对 EVAL_SET 中所有用例运行检索，计算 Recall@k 与 nDCG@k，输出 JSON 报告。
支持两种模式：

1. **在线模式**（默认）：连接真实向量库 / Neo4j，评测实际检索链路。
   需先运行 ``import_knowledge_docs.py`` 导入菜谱文档。
   用法: ``python -m backend.scripts.run_rag_eval --user-id 1 --top-k 4``

2. **离线模式**（``--offline``）：用 FakeKnowledgeService 基于文档名做
   子串匹配，验证评测集结构与指标计算逻辑的正确性，不依赖外部服务。
   适合 CI 回归。
   用法: ``python -m backend.scripts.run_rag_eval --offline --output eval.json``

输出格式: JSON 文件，含 evaluated_at / mean_recall_at_k / mean_ndcg_at_k /
results（每条用例的 query / recall / ndcg / hit_documents）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.schemas import (
    GraphSearchHit,
    KnowledgeSearchResponse,
    RetrievalDiagnostics,
    VectorSearchHit,
)
from app.services import rag_eval


class OfflineKnowledgeService:
    """基于文档名子串匹配的离线 KnowledgeService，用于 CI 回归。

    匹配规则：query 中包含 expected_document 的核心名（去 .md 后缀）时，
    视为命中。这验证了评测集结构与指标计算逻辑，但不评估真实检索质量。
    """

    def __init__(self, cases: list[rag_eval.RagEvalCase]) -> None:
        self._cases = cases
        # 从评测集提取所有期望文档名，作为离线"知识库"
        self._all_docs: list[str] = []
        for case in cases:
            self._all_docs.extend(case.expected_documents)

    async def search(
        self, query: str, user_id: int, top_k: int
    ) -> KnowledgeSearchResponse:
        del user_id, top_k
        # 按 query 与文档名的子串匹配度排序
        scored: list[tuple[str, float]] = []
        for doc in self._all_docs:
            core = doc.replace(".md", "")
            # 简化评分：query 包含核心名得高分，否则按字符重叠率
            if core in query:
                scored.append((doc, 0.95))
            else:
                overlap = sum(1 for ch in core if ch in query) / max(len(core), 1)
                if overlap > 0.3:
                    scored.append((doc, overlap * 0.5))
        scored.sort(key=lambda x: x[1], reverse=True)

        return KnowledgeSearchResponse(
            query=query,
            vector_hits=[
                VectorSearchHit(
                    document_id=f"offline-{idx}",
                    document_name=name,
                    category="菜谱",
                    content="离线模式不返回真实内容",
                    chunk_index=0,
                    score=score,
                )
                for idx, (name, score) in enumerate(scored[:4])
            ],
            graph_hits=[
                GraphSearchHit(
                    subject="本人",
                    relation="HAS_CONSTRAINT",
                    target="不吃辣",
                    detail="离线模式固定返回",
                )
            ],
            elapsed_ms=1,
            diagnostics=RetrievalDiagnostics(
                vector_store="offline",
                neo4j="offline",
                embedding="offline",
            ),
        )


async def run_offline(top_k: int) -> dict[str, Any]:
    """离线模式：验证评测集结构与指标计算逻辑。"""
    cases = rag_eval.EVAL_SET
    service = OfflineKnowledgeService(cases)
    response = await rag_eval.evaluate_retrieval(
        service, 1, Settings(_env_file=None), top_k=top_k, cases=cases
    )
    return response.model_dump()


async def run_online(user_id: int, top_k: int) -> dict[str, Any]:
    """在线模式：连接真实向量库 / Neo4j 评测实际检索质量。"""
    from app.services.knowledge import get_knowledge_service

    service = get_knowledge_service()
    response = await rag_eval.evaluate_retrieval(
        service, user_id, Settings(), top_k=top_k
    )
    return response.model_dump()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="离线模式（不依赖向量库/Neo4j，用于 CI 回归）",
    )
    parser.add_argument("--user-id", type=int, default=1, help="目标用户 ID（在线模式）")
    parser.add_argument("--top-k", type=int, default=4, help="检索 Top-K")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出 JSON 文件路径（不指定则打印到 stdout）",
    )
    args = parser.parse_args()

    if args.offline:
        result = asyncio.run(run_offline(args.top_k))
        mode_label = "离线"
    else:
        result = asyncio.run(run_online(args.user_id, args.top_k))
        mode_label = "在线"

    # 控制台摘要
    print(f"\n{'=' * 60}")
    print(f"RAG 检索质量评测（{mode_label}模式）")
    print(f"{'=' * 60}")
    print(f"用例数: {result['case_count']}")
    print(f"Top-K:  {result['top_k']}")
    print(f"Mean Recall@{result['top_k']}: {result['mean_recall_at_k']}")
    print(f"Mean nDCG@{result['top_k']}:    {result['mean_ndcg_at_k']}")
    print(f"\n详细结果:")
    for r in result["results"]:
        hit_docs = ", ".join(r["hit_document_names"]) or "无命中"
        print(f"  Recall={r['recall_at_k']:.2f} nDCG={r['ndcg_at_k']:.2f} | {r['query'][:30]}... → {hit_docs}")
    print(f"\n备注: {'; '.join(result['notes'])}")

    # 输出 JSON
    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output_json, encoding="utf-8")
        print(f"\n报告已写入: {args.output}")
    else:
        print(f"\n--- JSON 报告 ---\n{output_json}")

    # CI 回归门槛：离线模式要求 Recall >= 0.6
    if args.offline:
        threshold = 0.6
        if result["mean_recall_at_k"] < threshold:
            print(f"\nOFFLINE EVAL FAILED: Recall {result['mean_recall_at_k']} < {threshold}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"\nOFFLINE EVAL PASSED: Recall {result['mean_recall_at_k']} >= {threshold}")


if __name__ == "__main__":
    main()
