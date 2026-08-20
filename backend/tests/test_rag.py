import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.ai.llm import (
    DemoPlanGenerator,
    LLMGenerationError,
    OpenAICompatiblePlanGenerator,
    PlanDraft,
    token_sink,
)
from app.ai.workflow import SoloChefWorkflow
from app.core.config import Settings
from app.schemas import (
    GraphSearchHit,
    PlanningRequest,
    VectorSearchHit,
)
from app.services.conversation import SummaryStreamExtractor
from app.services.documents import DocumentParseError, DocumentProcessor
from app.services.embeddings import EmbeddingBackend, create_embedding_backend
from app.services.graph_store import Neo4jGraphStore
from app.services.milvus_store import MilvusVectorStore


class StubKnowledgeService:
    async def retrieve_graph(
        self,
        query: str,
        user_id: int,
    ) -> tuple[list[GraphSearchHit], str]:
        del query, user_id
        return [
            GraphSearchHit(
                subject="本人",
                relation="HAS_CONSTRAINT",
                target="不吃辣",
                detail="工作日晚上",
            )
        ], "connected"

    async def retrieve_vector(
        self,
        query: str,
        user_id: int,
        top_k: int,
        *,
        goal_type: str | None = None,
        meal_time: str | None = None,
    ) -> tuple[list[VectorSearchHit], str, str]:
        del query, user_id, top_k, goal_type, meal_time
        return [
            VectorSearchHit(
                document_id="doc-1",
                document_name="独居快手晚餐指南.md",
                category="菜谱",
                content="虾仁滑蛋盖饭约 18 分钟，不使用辣椒。",
                chunk_index=0,
                score=0.91,
            )
        ], "connected", "disabled"


def test_document_processor_splits_markdown() -> None:
    processor = DocumentProcessor(chunk_size=40, chunk_overlap=5)
    chunks = processor.split("标题\n\n" + "晚餐需要考虑成员忌口和固定日程。" * 8)
    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)


def test_document_processor_rejects_unknown_file_type() -> None:
    processor = DocumentProcessor(chunk_size=100, chunk_overlap=10)
    try:
        processor.parse("notes.docx", b"some content that is long enough")
    except DocumentParseError as exc:
        assert "不支持" in str(exc)
    else:
        raise AssertionError("unsupported file type should fail")


def test_demo_langgraph_workflow_has_parallel_specialists() -> None:
    request = PlanningRequest(prompt="安排一周晚餐和采购", budget=500)
    workflow = SoloChefWorkflow(
        knowledge=StubKnowledgeService(),
        generator=DemoPlanGenerator(),
    )

    response = asyncio.run(workflow.run(request))

    names = {step.name for step in response.trace}
    assert {"meal_agent", "shopping_agent", "budget_agent"} <= names
    assert len(response.meals) == 21
    assert response.budget.estimated <= request.budget
    assert response.conflicts == []


def test_workflow_uses_shopping_total_as_authoritative_budget_estimate() -> None:
    class OverBudgetGenerator(DemoPlanGenerator):
        async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
            draft = await super().generate(request, context)
            shopping = [
                item.model_copy(update={"price": 100.0}) for item in draft.shopping
            ]
            return draft.model_copy(update={"shopping": shopping})

    request = PlanningRequest(prompt="生成采购预算校验计划", budget=500)
    workflow = SoloChefWorkflow(
        knowledge=StubKnowledgeService(),
        generator=OverBudgetGenerator(),
    )

    response = asyncio.run(workflow.run(request))

    assert response.budget.estimated == 800
    assert response.budget.usage_percent == 160
    assert response.needs_manual_review is True
    assert any("采购清单估价 800 元超过预算 500 元" in item for item in response.conflicts)


def test_graph_search_uses_non_conflicting_search_parameter() -> None:
    calls: list[dict[str, object]] = []

    class Result:
        async def data(self) -> list[dict[str, str]]:
            return [
                {
                    "subject": "本人",
                    "relation": "HAS_CONSTRAINT",
                    "target": "不吃辣",
                    "detail": "",
                }
            ]

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def run(self, cypher: str, **kwargs: object) -> Result:
            assert "$search_text" in cypher
            calls.append(kwargs)
            return Result()

    class Driver:
        def session(self) -> Session:
            return Session()

    store = Neo4jGraphStore(Settings(_env_file=None))
    store._driver = Driver()  # type: ignore[assignment]
    hits = asyncio.run(store.search(9, "不吃辣"))
    assert calls[0] == {
        "user_id": 9,
        "search_text": "不吃辣",
        "keywords": [],
        "entity_kinds": [],
        "relations": [],
    }
    assert hits[0].target == "不吃辣"


@pytest.mark.asyncio
async def test_langgraph_resume_retries_only_pending_node() -> None:
    class CountingKnowledge(StubKnowledgeService):
        def __init__(self) -> None:
            self.calls = 0

        async def retrieve_graph(
            self,
            query: str,
            user_id: int,
        ) -> tuple[list[GraphSearchHit], str]:
            self.calls += 1
            return await super().retrieve_graph(query, user_id)

        async def retrieve_vector(
            self,
            query: str,
            user_id: int,
            top_k: int,
            *,
            goal_type: str | None = None,
            meal_time: str | None = None,
        ) -> tuple[list[VectorSearchHit], str, str]:
            self.calls += 1
            return await super().retrieve_vector(
                query, user_id, top_k, goal_type=goal_type, meal_time=meal_time
            )

    class FailOnceGenerator(DemoPlanGenerator):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
            self.calls += 1
            if self.calls == 1:
                raise LLMGenerationError("intentional checkpoint failure")
            return await super().generate(request, context)

    knowledge = CountingKnowledge()
    generator = FailOnceGenerator()
    workflow = SoloChefWorkflow(
        settings=Settings(_env_file=None, ai_fallback_enabled=False),
        knowledge=knowledge,
        generator=generator,
    )
    workflow.set_checkpointer(InMemorySaver())
    run_id = uuid4()
    with pytest.raises(LLMGenerationError, match="intentional checkpoint failure"):
        await workflow.run(PlanningRequest(prompt="恢复执行测试", budget=500), run_id=run_id)
    retrieval_calls = knowledge.calls

    result = await workflow.run(None, run_id=run_id, resume=True)

    assert len(result.trace) == 11
    assert result.trace[0].name == "constraint_parser"
    assert all(step.name != "intent" for step in result.trace)
    assert generator.calls == 2
    assert knowledge.calls == retrieval_calls


def test_summary_stream_extractor_handles_split_json_tokens() -> None:
    extractor = SummaryStreamExtractor()
    chunks = ['{"sum', 'mary": "', "计划已生成\\n可执行", '", "meals": []}']
    assert "".join(extractor.feed(chunk) for chunk in chunks) == "计划已生成\n可执行"
    assert extractor.done is True


@pytest.mark.asyncio
async def test_real_plan_generator_streams_model_chunks() -> None:
    request = PlanningRequest(prompt="真实模型流测试", budget=500)
    draft = await DemoPlanGenerator().generate(request, "")
    payload = draft.model_dump_json()

    class Chunk:
        def __init__(self, content: str) -> None:
            self.content = content

    class StreamingModel:
        async def astream(self, messages: object) -> AsyncIterator[Chunk]:
            del messages
            for offset in range(0, len(payload), 31):
                yield Chunk(payload[offset : offset + 31])

    generator = OpenAICompatiblePlanGenerator(
        Settings(
            _env_file=None,
            llm_provider="deepseek",
            llm_api_key="test-key",
        )
    )
    generator._model = cast(Any, StreamingModel())
    chunks: list[str] = []

    async def collect(chunk: str) -> None:
        chunks.append(chunk)

    context_token = token_sink.set(collect)
    try:
        result = await generator.generate(request, "stream context")
    finally:
        token_sink.reset(context_token)

    assert "".join(chunks) == payload
    assert result.summary == draft.summary


def test_embedding_backend_defaults_to_builtin_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 内置兜底模型（all-MiniLM-L6-v2）实例化不应触发网络下载：
    # 离线环境无法访问 HuggingFace，故用假模块替换 sentence_transformers，
    # 保持用例确定性（与下面 bge-m3 用例一致的隔离方式）。
    class FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs: object) -> None:
            del model_ref, kwargs

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    settings = Settings(_env_file=None, embedding_provider="default")
    backend = create_embedding_backend(settings)
    assert backend.is_bge_m3 is False
    assert backend.label == "内置轻量语义模型"
    assert "MiniLM" in backend.model_name


def test_embedding_backend_falls_back_when_bge_m3_path_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # bge-m3 路径缺失时回退到内置模型；用假模块隔离 sentence_transformers，
    # 避免离线环境下触发内置模型的网络下载。
    class FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs: object) -> None:
            del model_ref, kwargs

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    settings = Settings(
        _env_file=None,
        embedding_provider="bge-m3",
        embedding_model_path=str(tmp_path / "not-a-model"),
    )
    backend = create_embedding_backend(settings)
    assert backend.is_bge_m3 is False


def test_embedding_backend_loads_bge_m3_from_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    init_calls: list[dict[str, object]] = []

    class FakeVector:
        def __init__(self, values: list[float]) -> None:
            self._values = values

        def tolist(self) -> list[float]:
            return self._values

    class FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs: object) -> None:
            init_calls.append({"model_ref": model_ref, **kwargs})

        def encode(self, sentences: list[str], **kwargs: object) -> list[FakeVector]:
            del kwargs
            return [FakeVector([0.1, 0.2]) for _ in sentences]

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    settings = Settings(
        _env_file=None,
        embedding_provider="auto",
        embedding_model_path=str(model_dir),
        embedding_device="cpu",
    )
    backend = create_embedding_backend(settings)

    assert backend.is_bge_m3 is True
    assert backend.label == "本地语义模型 BGE-M3"
    assert init_calls == [
        {"model_ref": str(model_dir), "device": "cpu", "local_files_only": True}
    ]
    vectors = cast(Any, backend.function)(["晚餐吃什么", "采购清单"])
    # embedding 函数返回原始模型输出（numpy 类数组），由向量库统一转 list
    assert [v.tolist() for v in vectors] == [[0.1, 0.2], [0.1, 0.2]]


def test_embedding_backend_never_downloads_implicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    init_calls: list[dict[str, object]] = []

    class FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs: object) -> None:
            init_calls.append({"model_ref": model_ref, **kwargs})

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    # 显式清空本地模型路径：pymilvus 在 import 时调用 load_dotenv()，
    # 会把项目 .env 里的 EMBEDDING_MODEL_PATH 写入 os.environ，导致
    # `_env_file=None` 仍从环境变量读到机器本地路径，破坏本用例的确定性。
    settings = Settings(
        _env_file=None, embedding_provider="auto", embedding_model_path=""
    )
    backend = create_embedding_backend(settings)

    assert init_calls[0]["model_ref"] == "BAAI/bge-m3"
    assert init_calls[0]["local_files_only"] is True
    assert backend.is_bge_m3 is True


def test_vector_store_uses_dedicated_collection_for_bge_m3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 用假模块隔离 sentence_transformers，避免离线环境下触发内置模型的网络下载。
    class FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs: object) -> None:
            del model_ref, kwargs

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    settings = Settings(_env_file=None, milvus_collection="solochef_knowledge")
    store = MilvusVectorStore(settings)
    bge_backend = create_embedding_backend(
        Settings(_env_file=None, embedding_provider="default")
    )
    assert store.collection_name_for(bge_backend) == "solochef_knowledge"
    upgraded = bge_backend.__class__(
        function=bge_backend.function,
        model_name="BGE-M3 (stub)",
        label="本地语义模型 BGE-M3",
        is_bge_m3=True,
    )
    assert store.collection_name_for(upgraded) == "solochef_knowledge_bge_m3"


def test_replace_document_creates_collection_before_delete() -> None:
    # 回归：replace_document 在首次 delete 前必须先 _ensure_collection，
    # 否则全新部署冷启动会因 collection not found 抛错（bootstrap 无法入库）。
    calls: list[str] = []

    class FakeSchema:
        def add_field(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

    class FakeIndexParams:
        def add_index(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

    class FakeMilvusClient:
        def __init__(self) -> None:
            self._created = False

        def has_collection(self, collection_name: str) -> bool:
            del collection_name
            calls.append("has_collection")
            return self._created

        def describe_collection(self, collection_name: str) -> dict[str, object]:
            del collection_name
            calls.append("describe_collection")
            return {"fields": [{"name": "goal_type"}, {"name": "meal_time"}]}

        def create_schema(self, **kwargs: object) -> FakeSchema:
            del kwargs
            calls.append("create_schema")
            return FakeSchema()

        def prepare_index_params(self) -> FakeIndexParams:
            calls.append("prepare_index_params")
            return FakeIndexParams()

        def create_collection(self, **kwargs: object) -> None:
            del kwargs
            calls.append("create_collection")
            self._created = True

        def delete(self, **kwargs: object) -> None:
            del kwargs
            calls.append("delete")

        def upsert(self, **kwargs: object) -> None:
            del kwargs
            calls.append("upsert")

    fake_client = FakeMilvusClient()
    fake_backend = EmbeddingBackend(
        function=lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
        model_name="fake",
        label="fake",
        is_bge_m3=False,
    )
    store = MilvusVectorStore(Settings(_env_file=None))
    store._client = fake_client  # type: ignore[assignment]
    store._embedding = fake_backend  # type: ignore[assignment]

    asyncio.run(
        store.replace_document(
            name="测试文档.md",
            category="菜谱",
            chunks=["第一段", "第二段"],
            user_id=1,
            document_id="doc-1",
        )
    )

    assert "create_collection" in calls
    assert "delete" in calls
    assert calls.index("create_collection") < calls.index("delete")


def test_verifier_checks_user_allergy_constraints() -> None:
    workflow = SoloChefWorkflow(
        knowledge=StubKnowledgeService(),
        generator=DemoPlanGenerator(),
    )
    response = asyncio.run(
        workflow.run(
            PlanningRequest(prompt="生成一周菜单", budget=500),
            user_constraints=["虾过敏"],
        )
    )
    verifier = next(step for step in response.trace if step.name == "verifier")
    warnings = verifier.output["warnings"]
    assert isinstance(warnings, list)
    assert any("忌口或过敏食材" in str(item) and "虾" in str(item) for item in warnings)
