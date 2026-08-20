"""遍历 knowledge_docs/ 目录，批量导入菜谱 .md 到知识库（向量库 + Neo4j）。

导入后的文档可供 KnowledgeService 的 RAG 检索，扩充 Agent 生成计划时的
菜谱知识广度。category 取 .md 文件所在子目录名（如"独居快手晚餐"），
无子目录时回退"菜谱"。

用法:
    python -m backend.scripts.import_knowledge_docs \
        --src backend/app/data/knowledge_docs \
        --user-id 1

环境要求:
    向量库和 Neo4j 服务已启动（ingest_file 会写入向量库和图库）。
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.services.knowledge import get_knowledge_service


async def run(src: Path, user_id: int) -> None:
    """遍历 src 下所有 .md 文件，逐篇调用 ingest_file 导入知识库。"""
    service = get_knowledge_service()
    md_files = sorted(src.rglob("*.md"))
    if not md_files:
        print(f"未找到 .md 文件: {src}")
        return

    print(f"发现 {len(md_files)} 个 .md 文件，开始导入 (user_id={user_id})...\n")
    success = 0
    failed = 0
    for md_file in md_files:
        # category 取父目录名（如"独居快手晚餐"），文件直接在 src 下时回退"菜谱"
        category = md_file.parent.name if md_file.parent != src else "菜谱"
        payload = md_file.read_bytes()
        try:
            await service.ingest_file(
                name=md_file.name,
                category=category,
                payload=payload,
                user_id=user_id,
            )
            print(f"  [OK]   {md_file.name}")
            success += 1
        except Exception as exc:  # noqa: BLE001 — 批量导入需容错单篇失败
            print(f"  [FAIL] {md_file.name}: {type(exc).__name__}: {str(exc)[:80]}")
            failed += 1

    print(f"\n导入完成: {success} 成功, {failed} 失败 (user_id={user_id})")
    if failed:
        print("提示: 失败的文件可重跑本脚本补导入；请确认向量库/Neo4j 服务已启动。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--src",
        type=Path,
        default=Path("backend/app/data/knowledge_docs"),
        help="知识文档根目录（递归扫描 .md）",
    )
    parser.add_argument("--user-id", type=int, default=1, help="目标用户 ID")
    args = parser.parse_args()
    asyncio.run(run(args.src, args.user_id))


if __name__ == "__main__":
    main()
