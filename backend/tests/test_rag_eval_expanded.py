"""RAG 评测扩充测试（G15 CI 回归）。

验证扩充后的 EVAL_SET 结构完整性与离线评测逻辑正确性：
1. 评测集规模：从 5 条扩充到 20 条
2. 每条用例的 expected_documents 与菜谱知识库文档对齐
3. 离线评测脚本能正确计算 Recall@k / nDCG@k
4. CI 回归门槛：离线模式 Recall >= 0.6
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.services import rag_eval


# ── 双路径导入 helper ────────────────────────────────────────────────────
# CI 中 working-directory=backend, PYTHONPATH=. → from scripts.run_rag_eval
# 本地 PYTHONPATH=backend → from backend.scripts.run_rag_eval
# 两种环境下均能正确导入，避免硬编码绝对路径。

def _import_offline_module() -> Any:
    try:
        from scripts.run_rag_eval import OfflineKnowledgeService, main  # CI 路径
        return OfflineKnowledgeService, main
    except ImportError:
        from backend.scripts.run_rag_eval import OfflineKnowledgeService, main  # 本地路径
        return OfflineKnowledgeService, main


def _offline_service() -> Any:
    cls, _ = _import_offline_module()
    return cls(rag_eval.EVAL_SET)


def _offline_service_class() -> Any:
    cls, _ = _import_offline_module()
    return cls


def _offline_main() -> Any:
    _, main = _import_offline_module()
    return main


# ── 评测集结构完整性 ─────────────────────────────────────────────────────


def test_eval_set_expanded_to_20_cases():
    """Phase 3 扩充后评测集应至少有 20 条用例（5 原始 + 15 菜谱）。"""
    assert len(rag_eval.EVAL_SET) >= 20, (
        f"评测集应至少 20 条，实际 {len(rag_eval.EVAL_SET)} 条"
    )


def test_eval_set_cases_have_valid_structure():
    """每条评测用例必须有 query 和至少一个 expected_document。"""
    for idx, case in enumerate(rag_eval.EVAL_SET):
        assert case.query, f"第 {idx} 条用例 query 为空"
        assert case.expected_documents, f"第 {idx} 条用例 expected_documents 为空"
        for doc in case.expected_documents:
            assert isinstance(doc, str) and doc.strip(), (
                f"第 {idx} 条用例的 expected_document 含空值: {doc}"
            )


def test_eval_set_covers_recipe_documents():
    """扩充用例应覆盖已导入的 30 篇菜谱文档中的至少 15 篇。"""
    expected_docs = set()
    for case in rag_eval.EVAL_SET:
        expected_docs.update(case.expected_documents)

    # 菜谱文档以 .md 结尾
    recipe_docs = {doc for doc in expected_docs if doc.endswith(".md")}
    assert len(recipe_docs) >= 15, (
        f"菜谱文档评测用例应覆盖至少 15 篇，实际 {len(recipe_docs)} 篇"
    )


def test_eval_set_recipe_docs_match_knowledge_base():
    """评测用例中的菜谱文档名应与 knowledge_docs 目录下的文件对齐。"""
    knowledge_dir = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "data"
        / "knowledge_docs"
    )
    if not knowledge_dir.exists():
        pytest.skip("knowledge_docs 目录不存在，跳过文件对齐检查")

    actual_docs = {f.name for f in knowledge_dir.rglob("*.md")}
    expected_recipe_docs = {
        doc
        for case in rag_eval.EVAL_SET
        for doc in case.expected_documents
        if doc.endswith(".md")
    }

    missing = expected_recipe_docs - actual_docs
    assert not missing, (
        f"评测用例引用了 knowledge_docs 中不存在的文档: {missing}"
    )


# ── 离线评测逻辑 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_offline_eval_meets_ci_threshold():
    """离线模式评测应达到 CI 回归门槛 Recall >= 0.6。

    离线模式用子串匹配代替真实检索，主要验证：
    1. 评测集结构与指标计算逻辑无回归
    2. 每条用例的 query 能正确匹配到 expected_document
    """
    service = _offline_service()

    cases = rag_eval.EVAL_SET
    response = await rag_eval.evaluate_retrieval(
        service, 1, Settings(_env_file=None), top_k=4, cases=cases
    )

    assert response.case_count == len(cases)
    # CI 回归门槛
    assert response.mean_recall_at_k >= 0.6, (
        f"离线模式 Recall {response.mean_recall_at_k} 低于 CI 门槛 0.6"
    )
    assert 0 < response.mean_ndcg_at_k <= 1.0


@pytest.mark.asyncio
async def test_offline_eval_recipe_cases_hit():
    """离线模式下，菜谱类用例（.md 文档）应能命中至少 80%。"""
    service = _offline_service()

    cases = rag_eval.EVAL_SET
    response = await rag_eval.evaluate_retrieval(
        service, 1, Settings(_env_file=None), top_k=4, cases=cases
    )

    # 筛选菜谱类用例（expected_documents 含 .md 文件）
    recipe_case_indices = [
        idx
        for idx, case in enumerate(cases)
        if any(doc.endswith(".md") for doc in case.expected_documents)
    ]
    recipe_results = [response.results[i] for i in recipe_case_indices]

    hit_count = sum(1 for r in recipe_results if r.hit_document_names)
    hit_rate = hit_count / len(recipe_results) if recipe_results else 0

    assert hit_rate >= 0.8, (
        f"菜谱类用例命中率 {hit_rate:.1%} 低于 80%"
    )


def test_run_rag_eval_script_importable():
    """run_rag_eval 脚本应可正常导入，验证模块依赖完整。"""
    service_cls = _offline_service_class()
    main_fn = _offline_main()
    assert callable(main_fn)
    assert service_cls is not None
