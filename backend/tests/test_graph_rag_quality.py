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
    """模拟 FlagEmbedding 1.4+：reranker 由 ``FlagReranker`` 提供（devices 参数）。"""
    module = types.ModuleType("FlagEmbedding")

    class FakeFlagReranker:
        def __init__(self, model_ref, use_fp16=False, devices=None, **kwargs):
            capture.append(
                {"model_ref": model_ref, "use_fp16": use_fp16, "devices": devices, **kwargs}
            )

        def compute_score(self, pairs, **kwargs):
            del kwargs
            # 返回与输入等长、可降序排列的分数
            return [float(len(pair[1]) % 5) for pair in pairs]

    module.FlagReranker = FakeFlagReranker
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
    assert capture[0]["devices"] == ["cpu"]
    assert capture[0]["use_fp16"] is False
    scores = backend.rerank("本人不吃辣", ["虾仁滑蛋盖饭约18分钟", "番茄鸡蛋面"])
    assert len(scores) == 2
    assert all(isinstance(s, float) for s in scores)


def test_reranker_falls_back_to_direct_scoring(monkeypatch, tmp_path):
    """transformers 5.x 下 FlagEmbedding compute_score 抛 AttributeError 时，
    降级为直接用已加载 model/tokenizer 走标准 API 打分（sigmoid 归一化）。"""
    import math

    class _Tensor:
        def __init__(self, values):
            self._values = values

        def to(self, device):
            del device
            return self

        def view(self, dim):
            del dim
            return self

        def float(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return list(self._values)

    class _InnerModel:
        def parameters(self):
            yield types.SimpleNamespace(device="cpu")

        def __call__(self, **encoded):
            assert set(encoded) == {"input_ids", "attention_mask"}
            return types.SimpleNamespace(logits=_Tensor([2.0, -2.0]))

    class _Tokenizer:
        def __call__(self, queries, passages, **kwargs):
            assert len(queries) == len(passages) == 2
            assert kwargs["truncation"] is True
            return {"input_ids": _Tensor([1, 2]), "attention_mask": _Tensor([1, 1])}

    class IncompatibleFlagReranker:
        """模拟 FlagEmbedding <=1.4.0 + transformers 5.x：compute_score 崩溃。"""

        max_length = 512

        def __init__(self, model_ref, **kwargs):
            self.model = _InnerModel()
            self.tokenizer = _Tokenizer()

        def compute_score(self, pairs, **kwargs):
            raise AttributeError("XLMRobertaTokenizer has no attribute prepare_for_model")

    module = types.ModuleType("FlagEmbedding")
    module.FlagReranker = IncompatibleFlagReranker
    monkeypatch.setitem(sys.modules, "FlagEmbedding", module)

    from app.services.reranker import create_rerank_backend

    model_dir = tmp_path / "rerank"
    model_dir.mkdir()
    backend = create_rerank_backend(
        Settings(_env_file=None, rerank_enabled=True, rerank_model_path=str(model_dir))
    )
    assert backend is not None
    scores = backend.rerank("孩子不吃辣", ["清淡的虾仁滑蛋", "麻辣香锅"])
    assert len(scores) == 2
    assert all(0.0 < s < 1.0 for s in scores)
    assert scores[0] == pytest.approx(1.0 / (1.0 + math.exp(-2.0)), abs=1e-6)
    assert scores[1] == pytest.approx(1.0 / (1.0 + math.exp(2.0)), abs=1e-6)
    assert scores[0] > scores[1]


def test_reranker_factory_resolves_hf_cache_snapshot(monkeypatch, tmp_path):
    """未配置本地路径时，从 HF 缓存解析快照目录（不下载）。"""
    capture = []
    _fake_flag_embedding(monkeypatch, capture)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download",
        lambda repo_id, **kwargs: str(snapshot_dir),
    )
    from app.services.reranker import create_rerank_backend

    # 显式清空本地模型路径：pymilvus 在 import 时调用 load_dotenv()，
    # 会把项目 .env 里的 RERANK_MODEL_PATH 写入 os.environ，导致
    # `_env_file=None` 仍从环境变量读到机器本地路径，破坏本用例的确定性。
    backend = create_rerank_backend(
        Settings(_env_file=None, rerank_enabled=True, rerank_model_path="")
    )
    assert backend is not None
    assert capture[0]["model_ref"] == str(snapshot_dir)


def test_reranker_factory_never_downloads_implicitly(monkeypatch):
    """HF 缓存未命中时必须直接降级，绝不触发隐式下载。"""
    capture = []
    _fake_flag_embedding(monkeypatch, capture)
    calls = []

    def _snapshot_download(repo_id, **kwargs):
        calls.append({"repo_id": repo_id, **kwargs})
        raise FileNotFoundError("cache miss")

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot_download)
    from app.services.reranker import create_rerank_backend

    # 同上：显式清空本地模型路径，规避 pymilvus load_dotenv 的环境污染。
    backend = create_rerank_backend(
        Settings(_env_file=None, rerank_enabled=True, rerank_model_path="")
    )
    assert backend is None
    assert calls and calls[0]["repo_id"] == "BAAI/bge-reranker-v2-m3"
    assert calls[0]["local_files_only"] is True


def test_reranker_factory_disabled_returns_none():
    from app.services.reranker import create_rerank_backend

    assert create_rerank_backend(Settings(_env_file=None, rerank_enabled=False)) is None


# ── 实体/关系抽取降级 ──────────────────────────────────────────────


def test_entity_extractor_regex_fallback_without_llm():
    content = "菜系: 一人食\n约束: 不吃辣\n角色: 本人"
    knowledge = extract_knowledge(content, Settings(_env_file=None, llm_provider="demo"))
    kinds = {kind for kind, _ in knowledge.entities}
    assert "菜系" in kinds and "约束" in kinds
    # 正则级不产出关系
    assert knowledge.relations == []


# ── 查询改写规则级 ────────────────────────────────────────────────


def test_query_rewriter_rule_fallback_detects_kinds():
    spec = rewrite_query(
        "本人不吃辣，周三要快手晚餐？",
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
                subject="本人", relation="HAS_CONSTRAINT", target="不吃辣", detail=""
            )
        ],
        elapsed_ms=1,
        diagnostics=RetrievalDiagnostics(vector_store="connected", neo4j="connected", embedding="x"),
    )


@pytest.mark.asyncio
async def test_rag_eval_recall_and_ndcg():
    case = rag_eval.RagEvalCase(
        query="独居快手晚餐有哪些？",
        expected_documents=["独居快手晚餐指南"],
        expected_entity_kinds=["Member"],
    )
    service = FakeKnowledgeService(_response_with_docs(["独居快手晚餐指南", "控糖饮食原则"]))
    response = await rag_eval.evaluate_retrieval(
        service, 1, Settings(_env_file=None), top_k=4, cases=[case]
    )
    assert response.case_count == 1
    result = response.results[0]
    # 文档与实体类型均命中
    assert result.recall_at_k == 1.0
    assert result.hit_document_names == ["独居快手晚餐指南"]
    assert result.hit_entity_kinds == ["Member"]
    assert 0 < response.mean_ndcg_at_k <= 1.0


@pytest.mark.asyncio
async def test_rag_eval_no_hit_yields_zero():
    case = rag_eval.RagEvalCase(query="无关问题", expected_documents=["不存在的文档"])
    service = FakeKnowledgeService(_response_with_docs(["独居快手晚餐指南"]))
    response = await rag_eval.evaluate_retrieval(
        service, 1, Settings(_env_file=None), top_k=4, cases=[case]
    )
    assert response.results[0].recall_at_k == 0.0
    assert response.mean_recall_at_k == 0.0
