import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any, cast
from uuid import uuid4

import chromadb
from chromadb.api.models.Collection import Collection

from app.core.config import Settings
from app.schemas import KnowledgeDocument, VectorSearchHit
from app.services.embeddings import EmbeddingBackend, create_embedding_backend


class ChromaVectorStore:
    """Lazy Chroma adapter. Blocking client calls are moved off the event loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: chromadb.ClientAPI | None = None
        self._collection: Collection | None = None
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
        incompatible with the built-in model's 384-dim data."""
        suffix = "-bge-m3" if backend.is_bge_m3 else ""
        return f"{self._settings.chroma_collection}{suffix}"

    async def upsert_document(
        self,
        *,
        name: str,
        category: str,
        chunks: Sequence[str],
        user_id: int,
        document_id: str | None = None,
    ) -> KnowledgeDocument:
        collection = await self._get_collection()
        doc_id = document_id or str(uuid4())
        today = date.today().isoformat()
        ids = [f"{doc_id}:{index}" for index in range(len(chunks))]
        metadatas: list[dict[str, Any]] = [
            {
                "document_id": doc_id,
                "document_name": name,
                "category": category,
                "user_id": user_id,
                "chunk_index": index,
                "updated_at": today,
            }
            for index in range(len(chunks))
        ]
        await asyncio.to_thread(
            collection.upsert,
            ids=ids,
            documents=list(chunks),
            metadatas=cast(Any, metadatas),
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
    ) -> KnowledgeDocument:
        collection = await self._get_collection()
        await asyncio.to_thread(
            collection.delete,
            where={
                "$and": [
                    {"document_name": name},
                    {"user_id": user_id},
                ]
            },
        )
        return await self.upsert_document(
            name=name,
            category=category,
            chunks=chunks,
            user_id=user_id,
            document_id=document_id,
        )

    async def search(self, query: str, user_id: int, top_k: int) -> list[VectorSearchHit]:
        collection = await self._get_collection()
        count = await asyncio.to_thread(collection.count)
        if count == 0:
            return []
        result = await asyncio.to_thread(
            collection.query,
            query_texts=[query],
            n_results=min(top_k, count),
            where={"user_id": user_id},
            include=["documents", "metadatas", "distances"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[VectorSearchHit] = []
        for content, metadata, distance in zip(documents, metadatas, distances, strict=False):
            if content is None or metadata is None:
                continue
            hits.append(
                VectorSearchHit(
                    document_id=str(metadata["document_id"]),
                    document_name=str(metadata["document_name"]),
                    category=str(metadata["category"]),
                    content=content,
                    chunk_index=int(str(metadata["chunk_index"])),
                    score=round(max(0.0, 1.0 - float(distance or 0.0)), 4),
                )
            )
        return hits

    async def list_documents(self, user_id: int = 1) -> list[KnowledgeDocument]:
        collection = await self._get_collection()
        result = await asyncio.to_thread(
            collection.get,
            where={"user_id": user_id},
            include=["metadatas"],
        )
        grouped: dict[str, KnowledgeDocument] = {}
        for metadata in result.get("metadatas") or []:
            if metadata is None:
                continue
            document_id = str(metadata["document_id"])
            existing = grouped.get(document_id)
            if existing is None:
                grouped[document_id] = KnowledgeDocument(
                    id=document_id,
                    name=str(metadata["document_name"]),
                    category=str(metadata["category"]),
                    status="ready",
                    chunks=1,
                    updated_at=date.fromisoformat(str(metadata["updated_at"])),
                )
            else:
                existing.chunks += 1
        return sorted(grouped.values(), key=lambda item: item.updated_at, reverse=True)

    async def delete_document(self, document_id: str, user_id: int) -> None:
        collection = await self._get_collection()
        await asyncio.to_thread(
            collection.delete,
            where={"$and": [{"document_id": document_id}, {"user_id": user_id}]},
        )

    async def heartbeat(self) -> int:
        client = await self._get_client()
        return await asyncio.to_thread(client.heartbeat)

    async def stats(self, user_id: int = 1) -> tuple[int, int]:
        documents = await self.list_documents(user_id)
        return len(documents), sum(document.chunks for document in documents)

    async def _get_client(self) -> chromadb.ClientAPI:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = await asyncio.to_thread(
                    chromadb.HttpClient,
                    host=self._settings.chroma_host,
                    port=self._settings.chroma_port,
                    ssl=self._settings.chroma_ssl,
                )
        return self._client

    async def _get_collection(self) -> Collection:
        if self._collection is not None:
            return self._collection
        client = await self._get_client()
        backend = await self.ensure_embedding()
        async with self._lock:
            if self._collection is None:
                self._collection = await asyncio.to_thread(
                    client.get_or_create_collection,
                    name=self.collection_name_for(backend),
                    metadata={"hnsw:space": "cosine"},
                    embedding_function=cast(Any, backend.function),
                )
        return cast(Collection, self._collection)
