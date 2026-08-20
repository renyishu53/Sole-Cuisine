"""SoloChef RAG 真实端到端冒烟测试。

不依赖 pytest 的 stub/mock，直接连 docker-compose 起的 Milvus + Neo4j 底座，
调用 KnowledgeService 播种知识库并调用 knowledge.retrieve() 验证：
  - vector_status == "connected" 且 vector_hits 非空（真实向量召回）
  - neo4j_status == "connected" 且图谱实体已同步（真实图检索底座）

运行（需先 docker compose up -d mysql redis neo4j milvus）：
    cd backend
    DATABASE_URL=sqlite+aiosqlite:///:memory: \\
    LLM_PROVIDER=demo \\
    .venv/Scripts/python.exe scripts/rag_smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys

from app.core.config import get_settings
from app.services.knowledge import BOOTSTRAP_DOCUMENTS, KnowledgeService


async def main() -> int:
    # .env 已指向 localhost:19530(milvus)/7687(neo4j)/6379(redis)。
    # 强制 LLM=demo，使 query 改写 / 实体抽取走确定性规则回退，
    # 隔离检索基础设施测试、避免依赖外部 LLM 网络。
    settings = get_settings()
    settings.llm_provider = "demo"
    print(
        f"[config] milvus={settings.milvus_host}:{settings.milvus_port} "
        f"neo4j={settings.neo4j_uri} rag_enabled={settings.rag_enabled} "
        f"embedding={settings.embedding_provider}"
    )

    svc = KnowledgeService(settings)

    # 0) 清理残留集合，确保用当前（已修复的）embedding 函数重建，避免旧集合
    #    绑定的失效 embedding_function 被复用。
    try:
        client = await svc.vector_store._get_client()
        backend = await svc.vector_store.ensure_embedding()
        name = svc.vector_store.collection_name_for(backend)
        await asyncio.to_thread(client.delete_collection, name)
        print(f"[reset] 已删除旧集合 {name}（将用修复后的 embedding 函数重建）")
    except Exception as exc:  # 集合不存在时忽略
        print(f"[reset] 跳过（{type(exc).__name__}），将新建集合")

    # 1) 底座连通性自检
    status = await svc.status()
    print(
        f"[status] vector_store={status.vector_store} neo4j={status.neo4j} "
        f"embedding={status.embedding} collection={status.collection}"
    )

    # 2) 播种知识库：ingest_text 同时写 Milvus(向量) + Neo4j(图谱实体)
    print(f"[seed] 播种 {len(BOOTSTRAP_DOCUMENTS)} 篇文档到 Milvus + Neo4j ...")
    for name, category, content in BOOTSTRAP_DOCUMENTS:
        doc = await svc.ingest_text(
            name=name, category=category, content=content, user_id=1
        )
        print(f"  ✓ {doc.name} [{doc.category}] chunks={doc.chunks}")

    # 3) 一致性报告：确认双引擎都拿到文档/实体
    cons = await svc.consistency_report()
    print(
        f"[consistency] vector_docs={cons.vector_documents} "
        f"neo4j_docs={cons.neo4j_documents} neo4j_entities={cons.neo4j_entities} "
        f"consistent={cons.consistent}"
    )
    for note in cons.notes:
        print(f"    - {note}")

    # 4) 真实检索：断言 hits + status=connected
    queries = [
        "独居快手晚餐怎么安排",
        "控糖饮食有哪些原则",
        "工作日备餐如何减少浪费",
    ]
    checks: list[tuple[str, bool, str]] = []
    for q in queries:
        bundle = await svc.retrieve(q, user_id=1, top_k=settings.rag_top_k)
        v_ok = bool(bundle.vector_hits) and bundle.vector_status == "connected"
        g_ok = bundle.neo4j_status in ("connected", "connected(empty)")
        checks.append((q, v_ok and g_ok, bundle.neo4j_status if not g_ok else "ok"))
        print(f"\n[retrieve] {q!r}")
        print(
            f"  vector_status={bundle.vector_status} "
            f"rerank_status={bundle.rerank_status} "
            f"neo4j_status={bundle.neo4j_status}"
        )
        print(
            f"  vector_hits={len(bundle.vector_hits)} "
            f"graph_hits={len(bundle.graph_hits)}"
        )
        for h in bundle.vector_hits[:2]:
            snippet = (h.content or "")[:46].replace("\n", " ")
            print(f"    ▸ {h.document_name}[{h.category}] score={h.score} :: {snippet}...")
        for h in bundle.graph_hits[:3]:
            print(f"    ◆ graph {h.relation}: {h.head} → {h.tail}")

    await svc.close()

    print("\n=== RESULT ===")
    all_ok = all(ok for _, ok, _ in checks) and status.vector_store == "connected"
    for q, ok, extra in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {q} {'' if ok else '(' + extra + ')'}")
    if all_ok:
        print("PASS: 真实端到端检索成功（Milvus 已连接且有命中，Neo4j 已连接）")
        return 0
    print("FAIL: 真实检索未达预期")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
