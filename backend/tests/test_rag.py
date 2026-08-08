import asyncio
import sys
from collections.abc import AsyncIterator, Sequence
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
    CalendarEvent,
    GraphSearchHit,
    MemberProfile,
    PlanningRequest,
    VectorSearchHit,
)
from app.services.conversation import SummaryStreamExtractor
from app.services.documents import DocumentParseError, DocumentProcessor
from app.services.embeddings import create_embedding_backend
from app.services.graph_store import Neo4jGraphStore
from app.services.vector_store import ChromaVectorStore


class StubKnowledgeService:
    async def retrieve_graph(
        self,
        query: str,
        user_id: int,
        members: Sequence[MemberProfile] = (),
        events: Sequence[CalendarEvent] = (),
    ) -> tuple[list[GraphSearchHit], str]:
        del query, user_id, members, events
        return [
            GraphSearchHit(
                subject="本人",
                relation="HAS_CONSTRAINT",
                target="不吃辣",
                detail="工作日晚上",
            )
        ], "connected"

    async def retrieve_vector(
        self, query: str, user_id: int, top_k: int
    ) -> tuple[list[VectorSearchHit], str, str]:
        del query, user_id, top_k
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
    assert len(response.meals) == 7
    assert response.budget.estimated <= request.budget
    assert response.calendar.has_conflict is False
    assert response.conflicts == []


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
            members: Sequence[MemberProfile] = (),
            events: Sequence[CalendarEvent] = (),
        ) -> tuple[list[GraphSearchHit], str]:
            self.calls += 1
            return await super().retrieve_graph(query, user_id, members, events)

        async def retrieve_vector(
            self, query: str, user_id: int, top_k: int
        ) -> tuple[list[VectorSearchHit], str, str]:
            self.calls += 1
            return await super().retrieve_vector(query, user_id, top_k)

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


def test_embedding_backend_defaults_to_builtin_model() -> None:
    settings = Settings(_env_file=None, embedding_provider="default")
    backend = create_embedding_backend(settings)
    assert backend.is_bge_m3 is False
    assert backend.label == "内置轻量语义模型"
    assert "MiniLM" in backend.model_name


def test_embedding_backend_falls_back_when_bge_m3_path_missing(tmp_path: Path) -> None:
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
    assert vectors == [[0.1, 0.2], [0.1, 0.2]]


def test_embedding_backend_never_downloads_implicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    init_calls: list[dict[str, object]] = []

    class FakeSentenceTransformer:
        def __init__(self, model_ref: str, **kwargs: object) -> None:
            init_calls.append({"model_ref": model_ref, **kwargs})

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    settings = Settings(_env_file=None, embedding_provider="auto")
    backend = create_embedding_backend(settings)

    assert init_calls[0]["model_ref"] == "BAAI/bge-m3"
    assert init_calls[0]["local_files_only"] is True
    assert backend.is_bge_m3 is True


def test_vector_store_uses_dedicated_collection_for_bge_m3() -> None:
    settings = Settings(_env_file=None, chroma_collection="solochef_knowledge")
    store = ChromaVectorStore(settings)
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
    assert store.collection_name_for(upgraded) == "solochef_knowledge-bge-m3"


def test_verifier_checks_member_allergy_constraints() -> None:
    member = MemberProfile(
        id=99,
        name="过敏成员",
        role="本人",
        avatar="过",
        color="#46705d",
        preferences=[],
        constraints=["虾过敏"],
        availability="全天",
    )
    workflow = SoloChefWorkflow(
        knowledge=StubKnowledgeService(),
        generator=DemoPlanGenerator(),
    )
    response = asyncio.run(
        workflow.run(
            PlanningRequest(prompt="生成一周菜单", budget=500),
            members=[member],
        )
    )
    verifier = next(step for step in response.trace if step.name == "verifier")
    warnings = verifier.output["warnings"]
    assert isinstance(warnings, list)
    assert any("忌口或过敏食材" in str(item) and "虾" in str(item) for item in warnings)
