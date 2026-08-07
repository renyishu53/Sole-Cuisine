import sys
import types

import pytest

from app.core.config import Settings
from app.schemas import (
    GraphSearchHit,
    KnowledgeSearchResponse,
    RetrievalDiagnostics,
    VectorSearchHit,
)
from app.services import rag_eval
from app.services.entity_extractor import extract_knowledge
from app.services.query_rewriter import rewrite_query

# ── reranker 优雅降级（与 embeddings.py 测试一致）──────────────────────


def _fake_flag_embedding(monkeypatch, capture):
    module = types.ModuleType("FlagEmbedding")

    class FakeFlagModel:
        def __init__(self, model_ref, **kwargs):
            capture.append({"model_ref": model_ref, **kwargs})

        def compute_score(self, pairs, **kwargs):
            del kwargs
            # 返回与输入等长、可降序排列的分数
            return [float(len(pair[1]) % 5) for pair in pairs]

    module.FlagModel = FakeFlagModel
    monkeypatch.setitem(sys.modules, "FlagEmbedding", module)
    return module


def test_reranker_factory_returns_none_when_package_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "FlagEmbedding", None)  # 模拟未安装
    from app.services.reranker import create_rerank_backend

    backend = create_rerank_backend(Settings(_env_file=None, rerank_enabled=True))
    assert backend is None


def test_reranker_factory_loads_from_local_path(monkeypatch, tmp_path):
    capture = []
    _fake_flag_embedding(monkeypatch, capture)
    from app.services.reranker import create_rerank_backend

    model_dir = tmp_path / "rerank"
    model_dir.mkdir()
    backend = create_rerank_backend(
        Settings(_env_file=None, rerank_enabled=True, rerank_model_path=str(model_dir))
    )
    assert backend is not None
    assert capture[0]["model_ref"] == str(model_dir)
    assert capture[0]["local_files_only"] is True
    scores = backend.rerank("孩子不吃辣", ["虾仁滑蛋盖饭约18分钟", "番茄鸡蛋面"])
    assert len(scores) == 2
    assert all(isinstance(s, float) for s in scores)


def test_reranker_factory_never_downloads_implicitly(monkeypatch):
    capture = []
    _fake_flag_embedding(monkeypatch, capture)
    from app.services.reranker import create_rerank_backend

    backend = create_rerank_backend(Settings(_env_file=None, rerank_enabled=True))
    assert backend is not None
    assert capture[0]["model_ref"] == "BAAI/bge-reranker-v2-m3"
    assert capture[0]["local_files_only"] is True


def test_reranker_factory_disabled_returns_none():
    from app.services.reranker import create_rerank_backend

    assert create_rerank_backend(Settings(_env_file=None, rerank_enabled=False)) is None


# ── 实体/关系抽取降级 ──────────────────────────────────────────────


def test_entity_extractor_regex_fallback_without_llm():
    content = "菜系: 儿童友好\n约束: 不吃辣\n角色: 女儿"
    knowledge = extract_knowledge(content, Settings(_env_file=None, llm_provider="demo"))
    kinds = {kind for kind, _ in knowledge.entities}
    assert "菜系" in kinds and "约束" in kinds
    # 正则级不产出关系
    assert knowledge.relations == []


# ── 查询改写规则级 ────────────────────────────────────────────────


def test_query_rewriter_rule_fallback_detects_kinds():
    spec = rewrite_query(
        "孩子不吃辣，周三要快手晚餐？",
        Settings(_env_file=None, llm_provider="demo"),
    )
    assert "Member" in spec.entity_kinds
    assert "HAS_CONSTRAINT" in spec.relations
    assert any("辣" in kw for kw in spec.keywords)


# ── 离线检索质量评测 ──────────────────────────────────────────────


class FakeKnowledgeService:
    def __init__(self, response: KnowledgeSearchResponse) -> None:
        self._response = response

    async def search(self, query, user_id, top_k):
        del query, user_id, top_k
        return self._response


def _response_with_docs(names):
    return KnowledgeSearchResponse(
        query="q",
        vector_hits=[
            VectorSearchHit(
                document_id=f"d{i}",
                document_name=name,
                category="菜谱",
                content="x",
                chunk_index=0,
                score=0.9,
            )
            for i, name in enumerate(names)
        ],
        graph_hits=[
            GraphSearchHit(
                subject="小满", relation="HAS_CONSTRAINT", target="不吃辣", detail=""
            )
        ],
        elapsed_ms=1,
        diagnostics=RetrievalDiagnostics(chroma="connected", neo4j="connected", embedding="x"),
    )


@pytest.mark.asyncio
async def test_rag_eval_recall_and_ndcg():
    case = rag_eval.RagEvalCase(
        query="儿童友好的快手晚餐有哪些？",
        expected_documents=["儿童友好快手晚餐"],
        expected_entity_kinds=["Member"],
    )
    service = FakeKnowledgeService(_response_with_docs(["儿童友好快手晚餐", "控糖饮食原则"]))
    response = await rag_eval.evaluate_retrieval(
        service, 1, Settings(_env_file=None), top_k=4, cases=[case]
    )
    assert response.case_count == 1
    result = response.results[0]
    # 文档与实体类型均命中
    assert result.recall_at_k == 1.0
    assert result.hit_document_names == ["儿童友好快手晚餐"]
    assert result.hit_entity_kinds == ["Member"]
    assert 0 < response.mean_ndcg_at_k <= 1.0


@pytest.mark.asyncio
async def test_rag_eval_no_hit_yields_zero():
    case = rag_eval.RagEvalCase(query="无关问题", expected_documents=["不存在的文档"])
    service = FakeKnowledgeService(_response_with_docs(["儿童友好快手晚餐"]))
    response = await rag_eval.evaluate_retrieval(
        service, 1, Settings(_env_file=None), top_k=4, cases=[case]
    )
    assert response.results[0].recall_at_k == 0.0
    assert response.mean_recall_at_k == 0.0
