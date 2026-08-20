"""Graph RAG 检索质量离线评测。

构建评测集（query + 期望命中文档/实体类型），对当前检索链路计算
Recall@k 与 nDCG@k，并通过 ``GET /api/v1/admin/rag/eval`` 暴露。评测基于已落库
的向量库与 Neo4j 图数据，**不依赖 LLM 在线调用**，可重复执行、可回归对比。

指标说明：
- Recall@k：期望命中项是否在 top_k 结果中出现（命中占比）。
- nDCG@k：按命中位置折扣增益，越靠前命中得分越高；单相关项时等于 1/log2(pos+1)。
"""

from __future__ import annotations

import math
from datetime import datetime

from app.core.config import Settings
from app.schemas import (
    GraphSearchHit,
    KnowledgeSearchResponse,
    RagEvalCase,
    RagEvalResponse,
    RagEvalResult,
    VectorSearchHit,
)

# 评测集：与引导文档主题对齐，覆盖菜谱/营养/备餐/用户约束/日程。
# Phase 3 扩充：新增 30 篇菜谱知识库文档后，补充针对性评测用例，
# 覆盖高蛋白/低脂/素食/快手/汤面等典型查询场景。
EVAL_SET: list[RagEvalCase] = [
    # ── 原始引导文档评测（5 条）──
    RagEvalCase(
        query="独居快手晚餐有哪些？",
        expected_documents=["独居快手晚餐指南"],
    ),
    RagEvalCase(
        query="控糖的饮食原则是什么？",
        expected_documents=["控糖饮食原则"],
        expected_entity_kinds=["Recipe", "Ingredient"],
    ),
    RagEvalCase(
        query="独居备餐怎么规划？",
        expected_documents=["独居备餐与食材复用"],
    ),
    RagEvalCase(
        query="本人不吃辣，周三要快手晚餐，有什么推荐？",
        expected_documents=["独居快手晚餐指南", "控糖饮食原则"],
        expected_entity_kinds=["Member"],
    ),
    RagEvalCase(
        query="工作日 30 分钟以内的晚餐怎么安排？",
        expected_documents=["独居快手晚餐指南", "独居备餐与食材复用"],
    ),
    # ── 菜谱知识库扩充评测（15 条，针对 30 篇导入菜谱）──
    RagEvalCase(
        query="虾仁滑蛋盖饭怎么做？高蛋白低脂的快手晚餐",
        expected_documents=["虾仁滑蛋盖饭.md"],
    ),
    RagEvalCase(
        query="麻婆豆腐的做法，素食蛋白质来源",
        expected_documents=["麻婆豆腐.md"],
    ),
    RagEvalCase(
        query="番茄鸡蛋面简单做法，一人食面条",
        expected_documents=["番茄鸡蛋面.md"],
    ),
    RagEvalCase(
        query="清蒸鲈鱼的做法，低脂海鲜晚餐",
        expected_documents=["清蒸鲈鱼.md"],
    ),
    RagEvalCase(
        query="宫保鸡丁怎么炒？经典川菜家常做法",
        expected_documents=["宫保鸡丁.md"],
    ),
    RagEvalCase(
        query="土豆炖牛肉，适合周末慢炖的硬菜",
        expected_documents=["土豆炖牛肉.md"],
    ),
    RagEvalCase(
        query="蒜蓉西兰花，清淡蔬菜晚餐",
        expected_documents=["蒜蓉西兰花.md"],
    ),
    RagEvalCase(
        query="蛋炒饭一个人吃怎么做？剩饭利用",
        expected_documents=["蛋炒饭.md"],
    ),
    RagEvalCase(
        query="鱼香肉丝做法，下饭菜推荐",
        expected_documents=["鱼香肉丝.md"],
    ),
    RagEvalCase(
        query="肉末蒸蛋，嫩滑口感家常蒸菜",
        expected_documents=["肉末蒸蛋.md"],
    ),
    RagEvalCase(
        query="红烧茄子，素菜下饭做法",
        expected_documents=["红烧茄子.md"],
    ),
    RagEvalCase(
        query="葱爆羊肉，快手肉类晚餐",
        expected_documents=["葱爆羊肉.md"],
    ),
    RagEvalCase(
        query="地三鲜做法，东北家常素菜",
        expected_documents=["地三鲜.md"],
    ),
    RagEvalCase(
        query="番茄龙利鱼，低脂白肉鱼做法",
        expected_documents=["番茄龙利鱼.md"],
    ),
    RagEvalCase(
        query="韭菜炒鸡蛋，简单快手家常菜",
        expected_documents=["韭菜炒鸡蛋.md"],
    ),
    # ── 目标×餐次×约束多维评测（针对 P1 新增 frontmatter 文档）──
    RagEvalCase(
        query="减脂期晚餐吃什么？低脂高蛋白",
        expected_documents=["低脂凉拌鸡丝.md"],
    ),
    RagEvalCase(
        query="增肌期高蛋白午餐怎么吃",
        expected_documents=["香煎鸡胸配糙米.md"],
    ),
    RagEvalCase(
        query="有没有快手汤品和凉拌菜",
        expected_documents=["番茄蛋花汤.md", "凉拌黄瓜.md"],
    ),
    RagEvalCase(
        query="乳糖不耐的人怎么替代牛奶",
        expected_documents=["乳糖不耐替代指南.md"],
    ),
    RagEvalCase(
        query="独居早餐快速做法",
        expected_documents=["燕麦香蕉早餐杯.md", "鸡蛋吐司快手早餐.md"],
    ),
    RagEvalCase(
        query="剩饭剩菜怎么利用减少浪费",
        expected_documents=["剩饭剩菜变身.md"],
    ),
    RagEvalCase(
        query="一周食材怎么采购复用不浪费",
        expected_documents=["一周食材复用方案.md"],
    ),
    RagEvalCase(
        query="麸质过敏不能吃面食怎么办",
        expected_documents=["无麸质主食替代指南.md"],
    ),
    RagEvalCase(
        query="减脂期蒸菜推荐",
        expected_documents=["蒜蓉蒸娃娃菜.md"],
    ),
    RagEvalCase(
        query="增肌晚餐高蛋白推荐",
        expected_documents=["牛肉炖蛋.md"],
    ),
]


def _dcg_at_position(position: int) -> float:
    """单相关项在给定位置（1-based）的 DCG 增益。"""
    return 1.0 / math.log2(position + 1)


def _evaluate_case(
    case: RagEvalCase,
    response: KnowledgeSearchResponse,
    top_k: int,
) -> RagEvalResult:
    vector_hits: list[VectorSearchHit] = response.vector_hits[:top_k]
    graph_hits: list[GraphSearchHit] = response.graph_hits[:top_k]

    hit_doc_names = [
        hit.document_name
        for hit in vector_hits
        if hit.document_name in case.expected_documents
    ]
    graph_kinds = {
        coalesce_kind(hit.subject, hit.relation, hit.target)
        for hit in graph_hits
    }
    hit_kinds = [
        kind for kind in case.expected_entity_kinds if kind in graph_kinds
    ]

    recall = 0.0
    expected_total = len(case.expected_documents) + len(case.expected_entity_kinds)
    if expected_total > 0:
        recall = (len(hit_doc_names) + len(hit_kinds)) / expected_total

    # nDCG：文档命中（向量）与实体类型命中（图）各自按位置取最高增益后归一。
    ndcg = 0.0
    if expected_total > 0:
        gains: list[float] = []
        for expected in case.expected_documents:
            pos = _first_doc_position(vector_hits, expected)
            if pos is not None:
                gains.append(_dcg_at_position(pos))
        for expected in case.expected_entity_kinds:
            pos = _first_kind_position(graph_hits, expected)
            if pos is not None:
                gains.append(_dcg_at_position(pos))
        ndcg = sum(gains) / expected_total

    return RagEvalResult(
        query=case.query,
        recall_at_k=round(min(1.0, recall), 4),
        ndcg_at_k=round(min(1.0, ndcg), 4),
        hit_document_names=hit_doc_names,
        hit_entity_kinds=hit_kinds,
    )


def _first_doc_position(hits: list[VectorSearchHit], name: str) -> int | None:
    for index, hit in enumerate(hits, start=1):
        if hit.document_name == name:
            return index
    return None


def _first_kind_position(hits: list[GraphSearchHit], kind: str) -> int | None:
    for index, hit in enumerate(hits, start=1):
        if coalesce_kind(hit.subject, hit.relation, hit.target) == kind:
            return index
    return None


_KIND_BY_RELATION = {
    "HAS_CONSTRAINT": "Member",
    "PREFERS": "Member",
    "AVOIDS": "Member",
    "REQUIRES": "Recipe",
    "HAS_RECIPE": "Recipe",
    "HAS_TASK": "Task",
    "ASSIGNED_TO": "Task",
    "HAS_BUDGET": "Budget",
    "HAS_EVENT": "Event",
    "RELATION": "KnowledgeEntity",
}


def coalesce_kind(subject: str, relation: str, target: str) -> str:
    """从图命中推断一个可用于比对的实体类型。"""
    return _KIND_BY_RELATION.get(relation, "Entity")


async def evaluate_retrieval(
    knowledge_service: object,
    user_id: int,
    settings: Settings,
    top_k: int = 4,
    cases: list[RagEvalCase] | None = None,
) -> RagEvalResponse:
    """对评测集逐条运行检索并计算 Recall@k / nDCG@k。"""
    cases = cases or EVAL_SET
    embedding_label = settings.embedding_model
    reranker_label = "二阶段精排已启用" if settings.rerank_enabled else "未启用二阶段精排"

    results: list[RagEvalResult] = []
    for case in cases:
        response = await knowledge_service.search(  # type: ignore[attr-defined]
            case.query, user_id, top_k
        )
        results.append(_evaluate_case(case, response, top_k))

    case_count = len(results)
    mean_recall = round(sum(r.recall_at_k for r in results) / case_count, 4) if case_count else 0.0
    mean_ndcg = round(sum(r.ndcg_at_k for r in results) / case_count, 4) if case_count else 0.0

    notes = [
        f"评测用例 {case_count} 条，top_k={top_k}",
        f"语义向量：{embedding_label}",
        f"精排：{reranker_label}",
    ]
    empty_hits = all(not r.hit_document_names and not r.hit_entity_kinds for r in results)
    if empty_hits:
        notes.append("当前无命中，请先「初始化知识」或上传知识文档后再评测。")

    return RagEvalResponse(
        evaluated_at=datetime.now().isoformat(timespec="seconds"),
        embedding=embedding_label,
        reranker=reranker_label,
        top_k=top_k,
        case_count=case_count,
        mean_recall_at_k=mean_recall,
        mean_ndcg_at_k=mean_ndcg,
        results=results,
        notes=notes,
    )
