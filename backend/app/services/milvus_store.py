"""Milvus 向量存储适配器。

替代 ChromaVectorStore，使用 pymilvus MilvusClient API。
与 ChromaDB 的关键差异：
- 集合 schema 需手动定义（Chroma 自动推断）
- 无内置 embedding function，需手动 encode 文本为向量再插入
- 过滤表达式用 expr 字符串（Chroma 用 where dict）
- COSINE 指标下 distance 即相似度（Chroma 用距离，需 1-distance 转换）

稀疏混合检索（可选增强）：embedding 后端具备稀疏能力时，集合附加
``sparse_vector`` SPARSE_FLOAT_VECTOR 字段（SPARSE_INVERTED_INDEX/IP），
入库同写双向量；检索走 ``hybrid_search`` 双路召回（稠密 COSINE +
稀疏 IP），RRF 融合排序。集合 schema 形状与后端能力绑定——不匹配时
drop 重建，由 bootstrap 幂等重灌。
"""

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any, cast
from uuid import uuid4

from pymilvus import AnnSearchRequest, DataType, MilvusClient, RRFRanker

from app.core.config import Settings
from app.schemas import KnowledgeDocument, VectorSearchHit
from app.services.documents import DEFAULT_METADATA, METADATA_FIELDS
from app.services.embeddings import EmbeddingBackend, create_embedding_backend


# Reserved scope for built-in/domain knowledge. User-uploaded documents keep
# their real user_id and are never exposed through this scope.
PUBLIC_KNOWLEDGE_USER_ID = 0


class MilvusVectorStore:
    """Lazy Milvus adapter. Blocking client calls are moved off the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: MilvusClient | None = None
        self._embedding: EmbeddingBackend | None = None
        self._lock = asyncio.Lock()

    async def ensure_embedding(self) -> EmbeddingBackend:
        """Resolve the embedding backend once, off the event loop."""
        if self._embedding is not None:
            return self._embedding
        async with self._lock:
            if self._embedding is None:
                self._embedding = await asyncio.to_thread(
                    create_embedding_backend, self._settings
                )
        return self._embedding

    @property
    def embedding_model_name(self) -> str:
        """Technical embedding identifier for retrieval diagnostics."""
        if self._embedding is None:
            return "unresolved"
        return self._embedding.model_name

    def collection_name_for(self, backend: EmbeddingBackend) -> str:
        """BGE-M3 uses a dedicated collection: its 1024-dim vectors are
        incompatible with the lightweight model's 384-dim data.

        Milvus 集合名只允许数字、字母与下划线，故后缀用 ``_bge_m3`` 而非 ``-bge-m3``。
        """
        suffix = "_bge_m3" if backend.is_bge_m3 else ""
        return f"{self._settings.milvus_collection}{suffix}"

    @staticmethod
    def _dim_for(backend: EmbeddingBackend) -> int:
        """Vector dimension per embedding model."""
        return 1024 if backend.is_bge_m3 else 384

    @staticmethod
    def _escape_filter(value: str) -> str:
        """Escape double quotes in Milvus filter expressions."""
        return value.replace('"', '\\"')

    @staticmethod
    def _metadata_row(metadata: dict[str, str]) -> dict[str, str]:
        """把文档元数据规整为与集合 schema 对齐的标量行，缺失回退默认值。"""
        return {
            field: metadata.get(field, DEFAULT_METADATA[field])
            for field in METADATA_FIELDS
        }

    async def upsert_document(
        self,
        *,
        name: str,
        category: str,
        chunks: Sequence[str],
        user_id: int,
        document_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> KnowledgeDocument:
        client = await self._get_client()
        backend = await self.ensure_embedding()
        collection = self.collection_name_for(backend)
        await self._ensure_collection(collection, backend)

        doc_id = document_id or str(uuid4())
        today = date.today().isoformat()
        # 一次前向同时产出稠密+稀疏（后端无稀疏能力时 sparse 为 None）
        vectors, sparse_vectors = backend.encode_both(list(chunks))
        meta = metadata or {}

        data: list[dict[str, Any]] = []
        for index, chunk in enumerate(chunks):
            row: dict[str, Any] = {
                "id": f"{doc_id}:{index}",
                "vector": vectors[index],
                "document_id": doc_id,
                "document_name": name,
                "category": category,
                "user_id": user_id,
                "chunk_index": index,
                "content": chunk,
                "updated_at": today,
                **self._metadata_row(meta),
            }
            if sparse_vectors is not None:
                row["sparse_vector"] = sparse_vectors[index]
            data.append(row)
        await asyncio.to_thread(
            client.upsert,
            collection_name=collection,
            data=data,
        )
        return KnowledgeDocument(
            id=doc_id,
            name=name,
            category=category,
            status="ready",
            chunks=len(chunks),
            updated_at=date.fromisoformat(today),
        )

    async def replace_document(
        self,
        *,
        name: str,
        category: str,
        chunks: Sequence[str],
        user_id: int,
        document_id: str,
        metadata: dict[str, str] | None = None,
    ) -> KnowledgeDocument:
        client = await self._get_client()
        backend = await self.ensure_embedding()
        collection = self.collection_name_for(backend)
        # 先确保集合存在（幂等创建/迁移），否则全新部署首次 delete 会因
        # collection not found 抛错，bootstrap 无法冷启动。
        await self._ensure_collection(collection, backend)
        escaped_name = self._escape_filter(name)
        await asyncio.to_thread(
            client.delete,
            collection_name=collection,
            filter=f'document_name == "{escaped_name}" and user_id == {user_id}',
        )
        return await self.upsert_document(
            name=name,
            category=category,
            chunks=chunks,
            user_id=user_id,
            document_id=document_id,
            metadata=metadata,
        )

    async def search(
        self,
        query: str,
        user_id: int,
        top_k: int,
        *,
        goal_type: str | None = None,
        meal_time: str | None = None,
    ) -> list[VectorSearchHit]:
        client = await self._get_client()
        backend = await self.ensure_embedding()
        collection = self.collection_name_for(backend)

        exists = await asyncio.to_thread(client.has_collection, collection)
        if not exists:
            return []
        # 旧集合缺元数据/稀疏字段时在此重建（bootstrap 会幂等重灌），保证过滤字段可用。
        await self._ensure_collection(collection, backend)
        stats = await asyncio.to_thread(client.get_collection_stats, collection)
        if stats.get("row_count", 0) == 0:
            return []

        filter_expr = self._build_filter(user_id, goal_type, meal_time)
        output_fields = [
            "document_id",
            "document_name",
            "category",
            "content",
            "chunk_index",
            *METADATA_FIELDS,
        ]
        limit = min(top_k, stats.get("row_count", top_k))

        # 一次前向同时产出 query 的稠密+稀疏向量
        query_vectors, query_sparse = backend.encode_both([query])
        if query_sparse is not None:
            # 混合检索：稠密 COSINE + 稀疏 IP 双路召回，RRF 融合排序
            results = await asyncio.to_thread(
                client.hybrid_search,
                collection_name=collection,
                reqs=[
                    AnnSearchRequest(
                        data=[query_vectors[0]],
                        anns_field="vector",
                        param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                        limit=limit,
                        expr=filter_expr,
                    ),
                    AnnSearchRequest(
                        data=[query_sparse[0]],
                        anns_field="sparse_vector",
                        param={"metric_type": "IP"},
                        limit=limit,
                        expr=filter_expr,
                    ),
                ],
                ranker=RRFRanker(self._settings.sparse_rrf_k),
                limit=limit,
                output_fields=output_fields,
            )
            # RRF 融合分数非相似度量纲：以 top 命中为基准归一化到 (0, 1]
            rows = results[0] if results else []
            top_score = max(
                (float(row.get("distance", 0.0)) for row in rows), default=0.0
            ) or 1.0
            scores = [
                round(max(0.0, min(1.0, float(row.get("distance", 0.0)) / top_score)), 4)
                for row in rows
            ]
            return [
                self._to_hit(row, score)
                for row, score in zip(rows, scores, strict=False)
            ]

        query_vec = query_vectors[0]
        results = await asyncio.to_thread(
            client.search,
            collection_name=collection,
            data=[query_vec],
            filter=filter_expr,
            limit=limit,
            output_fields=output_fields,
        )
        hits: list[VectorSearchHit] = []
        for result in results[0]:
            distance = float(result.get("distance", 0.0))
            # COSINE 指标下 distance 即相似度（范围 [-1,1]，归一化后通常 [0,1]）
            score = round(max(0.0, min(1.0, distance)), 4)
            hits.append(self._to_hit(result, score))
        return hits

    def _to_hit(self, row: dict[str, Any], score: float) -> VectorSearchHit:
        """把 Milvus 检索行（``{id, distance, entity}``）转为 VectorSearchHit。

        ``client.search`` 与 ``client.hybrid_search`` 返回行结构一致，两路共用。
        """
        entity = row.get("entity", {}) or {}
        meta_row = {k: str(entity.get(k) or DEFAULT_METADATA[k]) for k in METADATA_FIELDS}
        return VectorSearchHit(
            document_id=str(entity.get("document_id", "")),
            document_name=str(entity.get("document_name", "")),
            category=str(entity.get("category", "")),
            content=str(entity.get("content", "")),
            chunk_index=int(entity.get("chunk_index", 0)),
            score=score,
            goal_type=meta_row["goal_type"],
            meal_time=meta_row["meal_time"],
            allergens=meta_row["allergens"],
            nutrition_focus=meta_row["nutrition_focus"],
        )

    def _build_filter(
        self,
        user_id: int,
        goal_type: str | None = None,
        meal_time: str | None = None,
    ) -> str:
        """构建 Milvus 过滤表达式：固定按 user_id 隔离，可选按目标/餐次过滤。

        过滤采用「目标 ∪ 通用兜底桶」的包含语义——目标型文档命中对应目标，
        默认桶（maintain/通用）文档对任何目标都可见，避免过滤后召回为空。
        """
        # Public bootstrap documents are visible to every user; private uploads
        # remain restricted to the requesting user's own scope.
        parts = [f"user_id in [{user_id}, {PUBLIC_KNOWLEDGE_USER_ID}]"]
        for field, value in (("goal_type", goal_type), ("meal_time", meal_time)):
            if value:
                allowed = {value, DEFAULT_METADATA[field]}
                quoted = ", ".join(f'"{self._escape_filter(item)}"' for item in allowed)
                parts.append(f"{field} in [{quoted}]")
        return " and ".join(parts)

    async def list_documents(self, user_id: int = 1) -> list[KnowledgeDocument]:
        client = await self._get_client()
        backend = await self.ensure_embedding()
        collection = self.collection_name_for(backend)

        exists = await asyncio.to_thread(client.has_collection, collection)
        if not exists:
            return []

        results = await asyncio.to_thread(
            client.query,
            collection_name=collection,
            filter=f"user_id in [{user_id}, {PUBLIC_KNOWLEDGE_USER_ID}]",
            output_fields=["document_id", "document_name", "category", "chunk_index", "updated_at"],
        )

        grouped: dict[str, KnowledgeDocument] = {}
        for row in results:
            document_id = str(row["document_id"])
            existing = grouped.get(document_id)
            if existing is None:
                grouped[document_id] = KnowledgeDocument(
                    id=document_id,
                    name=str(row["document_name"]),
                    category=str(row["category"]),
                    status="ready",
                    chunks=1,
                    updated_at=date.fromisoformat(str(row["updated_at"])),
                )
            else:
                existing.chunks += 1
        return sorted(grouped.values(), key=lambda item: item.updated_at, reverse=True)

    async def delete_document(self, document_id: str, user_id: int) -> None:
        client = await self._get_client()
        backend = await self.ensure_embedding()
        collection = self.collection_name_for(backend)
        escaped_id = self._escape_filter(document_id)
        await asyncio.to_thread(
            client.delete,
            collection_name=collection,
            filter=f'document_id == "{escaped_id}" and user_id == {user_id}',
        )

    async def heartbeat(self) -> int:
        """Milvus 无直接 heartbeat，用 list_collections 验证连通性。"""
        client = await self._get_client()
        await asyncio.to_thread(client.list_collections)
        return 1

    async def stats(self, user_id: int = 1) -> tuple[int, int]:
        documents = await self.list_documents(user_id)
        return len(documents), sum(document.chunks for document in documents)

    async def _get_client(self) -> MilvusClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                uri = f"http://{self._settings.milvus_host}:{self._settings.milvus_port}"
                kwargs: dict[str, Any] = {"uri": uri}
                if self._settings.milvus_user:
                    kwargs["user"] = self._settings.milvus_user
                    kwargs["password"] = self._settings.milvus_password
                self._client = await asyncio.to_thread(MilvusClient, **kwargs)
        return cast(MilvusClient, self._client)

    async def _ensure_collection(self, collection_name: str, backend: EmbeddingBackend) -> None:
        """幂等创建集合（含 schema + 索引），schema 形状与后端能力绑定。

        已存在时检查是否与后端能力匹配——缺元数据字段（旧 schema）或稀疏
        字段与 ``backend.supports_sparse`` 不一致（能力切换）均重建集合，
        由 ``bootstrap`` 幂等重灌，避免 schema 混用导致写入/过滤/检索报错。
        """
        client = await self._get_client()
        exists = await asyncio.to_thread(client.has_collection, collection_name)
        if exists:
            if await self._schema_matches(collection_name, backend.supports_sparse):
                return
            await asyncio.to_thread(client.drop_collection, collection_name)

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, max_length=128, is_primary=True)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=self._dim_for(backend))
        schema.add_field("document_id", DataType.VARCHAR, max_length=64)
        schema.add_field("document_name", DataType.VARCHAR, max_length=256)
        schema.add_field("category", DataType.VARCHAR, max_length=64)
        schema.add_field("user_id", DataType.INT64)
        schema.add_field("chunk_index", DataType.INT32)
        schema.add_field("content", DataType.VARCHAR, max_length=4000)
        schema.add_field("updated_at", DataType.VARCHAR, max_length=20)
        schema.add_field("goal_type", DataType.VARCHAR, max_length=32)
        schema.add_field("meal_time", DataType.VARCHAR, max_length=32)
        schema.add_field("allergens", DataType.VARCHAR, max_length=256)
        schema.add_field("nutrition_focus", DataType.VARCHAR, max_length=64)
        # 稀疏向量字段（lexical 权重）：仅后端支持时创建
        if backend.supports_sparse:
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128},
        )
        if backend.supports_sparse:
            index_params.add_index(
                field_name="sparse_vector",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="IP",
            )
        index_params.add_index(
            field_name="user_id",
            index_type="INVERTED",
        )

        await asyncio.to_thread(
            client.create_collection,
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )

    async def _schema_matches(
        self, collection_name: str, want_sparse: bool
    ) -> bool:
        """集合 schema 是否与后端能力匹配（含元数据字段与稀疏向量字段）。"""
        client = await self._get_client()
        info = await asyncio.to_thread(client.describe_collection, collection_name)
        field_names = {str(f.get("name")) for f in info.get("fields", [])}
        if "goal_type" not in field_names:
            return False
        return ("sparse_vector" in field_names) == want_sparse
