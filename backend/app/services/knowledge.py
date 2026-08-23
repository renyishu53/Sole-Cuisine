import asyncio
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import perf_counter

from app.core.config import Settings, get_settings
from app.schemas import (
    AIServiceStatus,
    GraphSearchHit,
    KnowledgeDocument,
    KnowledgeSearchResponse,
    RetrievalDiagnostics,
    SyncConsistencyResponse,
    VectorSearchHit,
)
from app.services.documents import DEFAULT_METADATA, DocumentParseError, DocumentProcessor
from app.services.embeddings import EmbeddingBackend
from app.services.entity_extractor import extract_knowledge
from app.services.graph_store import Neo4jGraphStore
from app.services.milvus_store import MilvusVectorStore
from app.services.query_rewriter import QuerySpec, rewrite_query
from app.services.reranker import RerankBackend, create_rerank_backend


@dataclass(frozen=True, slots=True)
class RetrievalBundle:
    vector_hits: list[VectorSearchHit]
    graph_hits: list[GraphSearchHit]
    vector_status: str
    neo4j_status: str
    rerank_status: str = "disabled"


BOOTSTRAP_DOCUMENTS = (
    (
        "独居快手晚餐指南.md",
        "菜谱",
        """# 独居快手晚餐指南

虾仁滑蛋盖饭：虾仁 100 克、鸡蛋 2 个，少油滑炒后搭配米饭，全程约 18 分钟。
适合时间紧张的工作日晚餐，一人份刚好不剩。

番茄鸡蛋面：番茄与鸡蛋提供酸甜口味，面条煮制约 20 分钟。单份控制盐量，避免刺激性调味。

晚餐优先蒸、煮、烩，工作日控制在 30 分钟内，并通过复用菌菇、青菜等食材减少独居采购浪费。""",
    ),
    (
        "控糖饮食原则.md",
        "营养",
        """# 控糖饮食原则

主食适量，优先全谷物；每餐搭配足量蔬菜和优质蛋白。避免含糖饮料、糖醋浓汁和额外添加糖。
烹饪方式优先清蒸、炖煮和少油快炒。用户有少糖约束时，菜单与采购清单都应避免高糖加工食品。""",
    ),
    (
        "独居备餐与食材复用.md",
        "备餐",
        """# 独居备餐与食材复用

独居备餐核心在于控制份量与减少浪费。周末集中采购后按餐分装冷藏，工作日取用即烹。
菌菇、青菜等耐储食材可在多餐中复用；鸡蛋、豆腐等通用食材灵活搭配不同菜式。
工作日晚间备餐控制在 20 分钟内，周日预留一次批量备餐时间，为下周工作日留出轻松晚餐。""",
    ),
)

# 菜谱/营养知识文档根目录（相对 app 包定位，供 bootstrap 自动遍历入库）。
_KNOWLEDGE_DOCS_DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge_docs"


class KnowledgeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.processor = DocumentProcessor(settings.chunk_size, settings.chunk_overlap)
        self.vector_store = MilvusVectorStore(settings)
        self.graph_store = Neo4jGraphStore(settings)
        self._reranker: RerankBackend | None = None
        self._reranker_loaded: bool = False

    async def ensure_reranker(self) -> RerankBackend | None:
        """解析 reranker 后端一次，进程内缓存；不可用则为 ``None``。"""
        if self._reranker_loaded:
            return self._reranker
        backend = await asyncio.to_thread(create_rerank_backend, self.settings)
        self._reranker = backend
        self._reranker_loaded = True
        return backend

    async def _sync_graph_knowledge(self, user_id: int, name: str, content: str) -> None:
        """抽取文档实体/关系并同步到 Neo4j，失败不阻断入库。"""
        knowledge = await asyncio.to_thread(extract_knowledge, content, self.settings)
        if knowledge.entities or knowledge.relations:
            with suppress(Exception):
                await self.graph_store.sync_document_knowledge(
                    user_id,
                    name,
                    knowledge.entities,
                    knowledge.relations,
                )

    async def ingest_text(
        self,
        *,
        name: str,
        category: str,
        content: str,
        user_id: int,
        metadata: dict[str, str] | None = None,
    ) -> KnowledgeDocument:
        chunks = self.processor.split(content)
        document = await self.vector_store.upsert_document(
            name=name,
            category=category,
            chunks=chunks,
            user_id=user_id,
            metadata=metadata,
        )
        await self._sync_graph_knowledge(user_id, name, content)
        return document

    async def ingest_file(
        self,
        *,
        name: str,
        category: str,
        payload: bytes,
        user_id: int,
    ) -> KnowledgeDocument:
        parsed = self.processor.parse(name, payload)
        return await self.ingest_text(
            name=parsed.name,
            category=category,
            content=parsed.content,
            user_id=user_id,
            metadata=parsed.metadata,
        )

    async def list_documents(self, user_id: int = 1) -> list[KnowledgeDocument]:
        return await self.vector_store.list_documents(user_id)

    async def bootstrap(
        self,
        user_id: int = 1,
        domain: dict[str, list[dict[str, object]]] | None = None,
    ) -> list[KnowledgeDocument]:
        documents = []
        for index, (name, category, content, metadata) in enumerate(
            self._bootstrap_items(), start=1
        ):
            chunks = self.processor.split(content)
            documents.append(
                await self.vector_store.replace_document(
                    name=name,
                    category=category,
                    chunks=chunks,
                    user_id=user_id,
                    document_id=f"bootstrap-{user_id}-{index}",
                    metadata=metadata,
                )
            )
            await self._sync_graph_knowledge(user_id, name, content)
        await self.graph_store.sync_user_context(user_id, None, domain)
        return documents

    def _bootstrap_items(self) -> list[tuple[str, str, str, dict[str, str]]]:
        """汇总引导文档与 ``knowledge_docs/`` 目录文档为 ``(名称, 分类, 正文, 元数据)``。

        引导文档保留在首位（无 frontmatter，元数据取默认），目录文档按路径稳定排序，
        使 bootstrap 具备幂等性——同一文档始终映射到同一 document_id。
        """
        items: list[tuple[str, str, str, dict[str, str]]] = [
            (name, category, content, dict(DEFAULT_METADATA))
            for name, category, content in BOOTSTRAP_DOCUMENTS
        ]
        items.extend(self._directory_items())
        return items

    def _directory_items(self) -> list[tuple[str, str, str, dict[str, str]]]:
        """遍历 knowledge_docs/ 下所有 .md，解析 frontmatter 得到元数据。

        category 取文件所在子目录名；解析失败的单篇静默跳过，不阻断整体入库。
        """
        items: list[tuple[str, str, str, dict[str, str]]] = []
        if not _KNOWLEDGE_DOCS_DIR.is_dir():
            return items
        for path in sorted(_KNOWLEDGE_DOCS_DIR.rglob("*.md"), key=lambda p: p.as_posix()):
            category = path.parent.name if path.parent != _KNOWLEDGE_DOCS_DIR else "菜谱"
            try:
                parsed = self.processor.parse(path.name, path.read_bytes())
            except DocumentParseError:
                continue
            items.append((parsed.name, category, parsed.content, parsed.metadata))
        return items

    async def retrieve_vector(
        self,
        query: str,
        user_id: int,
        top_k: int,
        *,
        goal_type: str | None = None,
        meal_time: str | None = None,
    ) -> tuple[list[VectorSearchHit], str, str]:
        """首阶段向量召回 + 可选二阶段 rerank。

        返回 (hits, vector_status, rerank_status)。``goal_type``/``meal_time`` 为
        可选元数据过滤（含「目标 ∪ 通用」包含语义），由规划侧注入用户目标。
        """
        if not self.settings.rag_enabled:
            return [], "disabled", "disabled"
        try:
            reranker = await self.ensure_reranker()
            candidate_k = top_k * self.settings.rerank_candidate_multiplier if reranker else top_k
            hits = await self.vector_store.search(
                query, user_id, candidate_k, goal_type=goal_type, meal_time=meal_time
            )
            if reranker and hits:
                scores = reranker.rerank(query, [hit.content for hit in hits])
                ranked = sorted(
                    zip(hits, scores, strict=False),
                    key=lambda pair: pair[1],
                    reverse=True,
                )
                hits = [hit for hit, _ in ranked[:top_k]]
                return hits, "connected", "reranked"
            return hits[:top_k], "connected", "disabled"
        except Exception as exc:
            return [], f"unavailable: {type(exc).__name__}", "disabled"

    async def retrieve_graph(
        self,
        query: str,
        user_id: int,
        domain: dict[str, list[dict[str, object]]] | None = None,
        query_spec: QuerySpec | None = None,
    ) -> tuple[list[GraphSearchHit], str]:
        if not self.settings.rag_enabled:
            return [], "disabled"
        try:
            await self.graph_store.sync_user_context(user_id, None, domain)
            spec = query_spec or rewrite_query(query, self.settings)
            return await self.graph_store.search(user_id, query, spec), "connected"
        except Exception as exc:
            return [], f"unavailable: {type(exc).__name__}"

    async def retrieve(
        self,
        query: str,
        user_id: int,
        top_k: int,
        domain: dict[str, list[dict[str, object]]] | None = None,
        goal_type: str | None = None,
        meal_time: str | None = None,
    ) -> RetrievalBundle:
        query_spec = rewrite_query(query, self.settings) if self.settings.rag_enabled else None
        vector_result, graph_result = await asyncio.gather(
            self.retrieve_vector(
                query, user_id, top_k, goal_type=goal_type, meal_time=meal_time
            ),
            self.retrieve_graph(query, user_id, domain, query_spec),
        )
        vector_hits, vector_status, rerank_status = vector_result
        graph_hits, neo4j_status = graph_result
        return RetrievalBundle(
            vector_hits, graph_hits, vector_status, neo4j_status, rerank_status
        )

    async def search(
        self,
        query: str,
        user_id: int,
        top_k: int,
        *,
        domain: dict[str, list[dict[str, object]]] | None = None,
        goal_type: str | None = None,
        meal_time: str | None = None,
    ) -> KnowledgeSearchResponse:
        start = perf_counter()
        bundle = await self.retrieve(
            query, user_id, top_k, domain, goal_type=goal_type, meal_time=meal_time
        )
        embedding = await self.vector_store.ensure_embedding()
        return KnowledgeSearchResponse(
            query=query,
            vector_hits=bundle.vector_hits,
            graph_hits=bundle.graph_hits,
            elapsed_ms=max(1, round((perf_counter() - start) * 1000)),
            diagnostics=RetrievalDiagnostics(
                vector_store=bundle.vector_status,
                neo4j=bundle.neo4j_status,
                embedding=embedding.model_name,
                rerank=bundle.rerank_status,
                sparse=self._sparse_status(embedding, bundle.vector_status),
            ),
        )

    @staticmethod
    def _sparse_status(embedding: EmbeddingBackend, vector_status: str) -> str:
        """稀疏检索诊断：后端具备能力且向量路连通 → hybrid，否则 disabled。"""
        if not embedding.supports_sparse:
            return "disabled"
        return "hybrid (dense+lexical)" if vector_status == "connected" else vector_status

    async def status(self, user_id: int = 1) -> AIServiceStatus:
        vector_status = "connected"
        documents = chunks = 0
        try:
            await self.vector_store.heartbeat()
            documents, chunks = await self.vector_store.stats(user_id)
        except Exception as exc:
            vector_status = f"unavailable: {type(exc).__name__}"
        neo4j_status = "connected"
        try:
            await self.graph_store.verify()
        except Exception as exc:
            neo4j_status = f"unavailable: {type(exc).__name__}"
        embedding = await self.vector_store.ensure_embedding()
        reranker = await self.ensure_reranker()
        reranker_label = reranker.label if reranker is not None else "未启用二阶段精排"
        sparse_label = (
            "BGE-M3 lexical 稀疏检索（稠密+稀疏双路 RRF）"
            if embedding.supports_sparse
            else "未启用稀疏检索（纯稠密召回）"
        )
        return AIServiceStatus(
            rag_enabled=self.settings.rag_enabled,
            embedding=embedding.label,
            reranker=reranker_label,
            sparse=sparse_label,
            llm_mode=(self.settings.llm_provider if self.settings.real_llm_enabled else "demo"),
            langgraph="compiled",
            vector_store=vector_status,
            neo4j=neo4j_status,
            collection=self.vector_store.collection_name_for(embedding),
            documents=documents,
            chunks=chunks,
            llm_provider=self.settings.llm_provider,
            llm_model=self.settings.llm_model,
            llm_configured=self.settings.real_llm_enabled,
        )

    async def consistency_report(self, user_id: int = 1) -> SyncConsistencyResponse:
        """比较向量库文档/片段与 Neo4j Document/实体 的同步一致性。

        检测两类偏差：向量库有而 Neo4j 缺失的文档（图谱未同步）、Neo4j 有而
        向量库缺失的文档（孤儿节点）。不依赖外部服务可达性——不可用时返回对应
        状态说明，而非抛错。
        """
        vector_status = "connected"
        vector_docs: list[str] = []
        vector_chunks = 0
        try:
            documents = await self.vector_store.list_documents(user_id)
            vector_docs = [doc.name for doc in documents]
            vector_chunks = sum(doc.chunks for doc in documents)
        except Exception as exc:
            vector_status = f"unavailable: {type(exc).__name__}"

        neo4j_status = "connected"
        neo4j_docs: list[str] = []
        neo4j_entities = 0
        try:
            neo4j_docs = await self.graph_store.list_document_names(user_id)
            neo4j_entities = await self.graph_store.count_entities(user_id)
        except Exception as exc:
            neo4j_status = f"unavailable: {type(exc).__name__}"

        missing_in_neo4j = sorted(set(vector_docs) - set(neo4j_docs))
        orphan_in_neo4j = sorted(set(neo4j_docs) - set(vector_docs))
        consistent = (
            vector_status == "connected"
            and neo4j_status == "connected"
            and not missing_in_neo4j
            and not orphan_in_neo4j
        )
        notes: list[str] = []
        if vector_status != "connected":
            notes.append(f"向量知识库不可用：{vector_status}")
        if neo4j_status != "connected":
            notes.append(f"关系图谱(Neo4j)不可用：{neo4j_status}")
        if missing_in_neo4j:
            notes.append(f"{len(missing_in_neo4j)} 份文档仅存在于向量库，图谱未同步")
        if orphan_in_neo4j:
            notes.append(f"{len(orphan_in_neo4j)} 份文档仅存在于图谱（孤儿节点）")
        if not notes:
            notes.append("向量库与 Neo4j 文档索引一致")
        return SyncConsistencyResponse(
            vector_status=vector_status,
            neo4j_status=neo4j_status,
            vector_documents=len(vector_docs),
            vector_chunks=vector_chunks,
            neo4j_documents=len(neo4j_docs),
            neo4j_entities=neo4j_entities,
            missing_in_neo4j=missing_in_neo4j,
            orphan_in_neo4j=orphan_in_neo4j,
            consistent=consistent,
            notes=notes,
        )

    async def close(self) -> None:
        await self.graph_store.close()


@lru_cache
def get_knowledge_service() -> KnowledgeService:
    return KnowledgeService(get_settings())
