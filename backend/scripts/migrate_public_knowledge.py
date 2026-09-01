"""将历史公共知识从旧 user_id 迁移到 0，并删除同名重复索引。

只处理源码内置文档名称，不触碰其他用户的私有上传文档。
用法：``python -m scripts.migrate_public_knowledge --legacy-user-id 1``
"""
from __future__ import annotations

import argparse
import asyncio

from app.services.knowledge import get_knowledge_service
from app.services.milvus_store import PUBLIC_KNOWLEDGE_USER_ID


async def migrate(legacy_user_id: int) -> tuple[int, int]:
    service = get_knowledge_service()
    items = service._bootstrap_items()  # noqa: SLF001 - 与启动 bootstrap 使用同一清单
    migrated = 0
    removed = 0
    for index, (name, category, content, metadata) in enumerate(items, start=1):
        chunks = service.processor.split(content)
        await service.vector_store.replace_document(
            name=name,
            category=category,
            chunks=chunks,
            user_id=PUBLIC_KNOWLEDGE_USER_ID,
            document_id=f"bootstrap-{PUBLIC_KNOWLEDGE_USER_ID}-{index}",
            metadata=metadata,
        )
        await service._sync_graph_knowledge(PUBLIC_KNOWLEDGE_USER_ID, name, content)  # noqa: SLF001
        migrated += 1
        if legacy_user_id != PUBLIC_KNOWLEDGE_USER_ID:
            legacy_docs = await service.vector_store.list_documents(legacy_user_id)
            for document in legacy_docs:
                if document.name == name:
                    await service.vector_store.delete_document(document.id, legacy_user_id)
                    await service.graph_store.delete_document(legacy_user_id, name)
                    removed += 1
    return migrated, removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-user-id", type=int, default=1)
    args = parser.parse_args()
    migrated, removed = asyncio.run(migrate(args.legacy_user_id))
    print(f"公共文档已迁移 {migrated} 份，删除旧作用域重复索引 {removed} 份")


if __name__ == "__main__":
    main()
