"""调用 DeepSeek LLM 批量生成独居菜谱知识文档，写入 knowledge_docs 目录。

生成的 .md 文件供 import_knowledge_docs.py 导入 Milvus + Neo4j 知识库，
扩充 RAG 检索的菜谱知识广度（从 3 篇扩充到 30+ 篇）。

用法:
    python -m backend.scripts.generate_recipe_docs \
        --out backend/app/data/knowledge_docs/独居快手晚餐

环境要求:
    .env 配置 LLM_PROVIDER（非 demo）和 LLM_API_KEY，即 real_llm_enabled=True。
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from langchain_openai import ChatOpenAI

from app.core.config import get_settings

# 30 个独居快手菜谱主题（覆盖肉/蛋/豆/蔬/水产/主食，适配独居单份量）
TOPICS: list[str] = [
    "虾仁滑蛋盖饭", "番茄鸡蛋面", "青椒土豆丝", "蒜蓉西兰花",
    "香菇滑鸡", "麻婆豆腐", "葱爆羊肉", "清蒸鲈鱼",
    "蚝油生菜", "木耳炒肉片", "黄瓜炒鸡蛋", "芹菜炒香干",
    "红烧茄子", "可乐鸡翅", "番茄龙利鱼", "回锅肉",
    "鱼香肉丝", "宫保鸡丁", "地三鲜", "干煸豆角",
    "番茄牛腩", "蒜苔炒肉", "韭菜炒鸡蛋", "菠菜拌粉丝",
    "土豆炖牛肉", "虎皮青椒", "肉末蒸蛋", "清炒空心菜",
    "蛋炒饭", "葱油拌面",
]

# 统一文档模板，保证 chunking 和检索一致性（## 章节分隔利于 RecursiveCharacterTextSplitter）
_TEMPLATE = """你是 SoloChef 的菜谱知识库编辑。请为独居人士生成一篇快手菜谱知识文档，严格遵守以下 Markdown 结构（不要添加额外章节或代码块）：

# {topic}

## 食材
列出 3-5 种食材及单人份用量（如"虾仁 100g、鸡蛋 2 个"）

## 步骤
编号列出 3-5 个简洁步骤，每步一行

## 营养要点
2-3 条营养特点（蛋白质来源、热量区间、适合的饮食目标）

## 独居适配
2-3 条独居场景建议（备餐时长、食材复用、份量控制）

要求：全文中文，简洁实用，总长度 200-300 字。直接输出 Markdown，不要包裹在代码块里。"""

# 并发上限，避免触发 API 限流
_MAX_CONCURRENCY = 5


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """单篇生成结果。"""

    topic: str
    content: str | None  # None 表示生成失败


async def _generate_one(topic: str, model: ChatOpenAI) -> str:
    """调用 LLM 生成单篇菜谱文档。"""
    response = await model.ainvoke([("user", _TEMPLATE.format(topic=topic))])
    content = response.content if isinstance(response.content, str) else str(response.content)
    # 剥离 LLM 偶尔包裹的代码块围栏
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines)
    return content.strip()


async def _bounded_generate(
    topic: str, model: ChatOpenAI, semaphore: asyncio.Semaphore
) -> GenerationResult:
    """带限流的单篇生成，EAFP 捕获异常避免单篇失败中断整体。"""
    async with semaphore:
        try:
            content = await _generate_one(topic, model)
            print(f"  [OK]   {topic}")
            return GenerationResult(topic=topic, content=content)
        except Exception as exc:  # noqa: BLE001 — 批量生成需容错单篇失败
            print(f"  [FAIL] {topic}: {type(exc).__name__}: {str(exc)[:80]}")
            return GenerationResult(topic=topic, content=None)


async def run(out_dir: Path) -> None:
    """批量生成菜谱文档并写入 out_dir。"""
    settings = get_settings()
    if not settings.real_llm_enabled:
        raise SystemExit(
            "LLM 未配置（llm_provider=demo），无法生成。"
            "请在 .env 配置 LLM_PROVIDER 和 LLM_API_KEY。"
        )

    model = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=0.7,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        max_tokens=600,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    tasks = [_bounded_generate(topic, model, semaphore) for topic in TOPICS]
    results = await asyncio.gather(*tasks)

    written = 0
    for result in results:
        if result.content is None:
            continue
        path = out_dir / f"{result.topic}.md"
        path.write_text(result.content, encoding="utf-8")
        written += 1

    print(f"\n生成完成: {written}/{len(TOPICS)} 篇 -> {out_dir}")
    if written < len(TOPICS):
        print(f"警告: {len(TOPICS) - written} 篇失败，可重跑脚本补生成（已存在的文件会被覆盖）")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("backend/app/data/knowledge_docs/独居快手晚餐"),
        help="输出目录",
    )
    args = parser.parse_args()
    asyncio.run(run(args.out))


if __name__ == "__main__":
    main()
