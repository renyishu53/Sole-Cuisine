"""执行反馈闭环：把执行结果从 PostgreSQL 回流到 Neo4j 与 Chroma。

项目没有 ORM 事件钩子（``event.listen`` / ``after_commit``），因此"计划 → 执行 →
反馈 → 检索/记忆"这一环必须显式编排。本模块提供唯一入口
:meth:`FeedbackLoopService.capture`：

1. **落库**：写入 ``plan_feedback`` 偏差表，记录主观反馈与客观偏差；
2. **回图谱**：``(:Family)-[:HAS_FEEDBACK]->(:FeedbackSignal)-[:ABOUT]->(:KnowledgeEntity)``，
   正/负向信号额外连到 ``Preference`` 节点；
3. **回向量库**：按反馈类型维护一份滚动文档（固定 ``document_id``），
   下一轮 RAG 检索即可召回"上次这道菜太辣"这类历史反馈。

外部依赖（Neo4j / Chroma）不可用时只降级记录同步状态，绝不影响主业务链路；
未同步的记录留在 ``plan_feedback.synced_to_*`` 上，可由补偿任务重放。
"""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlanFeedback
from app.repositories.feedback import FeedbackRepository
from app.services.knowledge import KnowledgeService, get_knowledge_service

logger = logging.getLogger(__name__)

# ── 反馈类型 ────────────────────────────────────────────────────────────
TASK_COMPLETION = "task_completion"
MEAL_REPLACEMENT = "meal_replacement"
MEAL_RATING = "meal_rating"
SHOPPING_VERIFICATION = "shopping_verification"
EXPENSE_RECORD = "expense_record"

FEEDBACK_LABELS: dict[str, str] = {
    TASK_COMPLETION: "任务执行",
    MEAL_REPLACEMENT: "餐食替换",
    MEAL_RATING: "餐食评价",
    SHOPPING_VERIFICATION: "采购核销",
    EXPENSE_RECORD: "支出记录",
}

# ── 情感与口味词典（无 LLM 时的确定性判定）────────────────────────────
_POSITIVE_PHRASES: tuple[str, ...] = (
    "好吃", "喜欢", "不错", "满意", "很棒", "太棒", "省时", "省钱", "方便",
    "顺利", "提前完成", "还想吃", "再来", "赞", "省事", "合口味",
)
_NEGATIVE_PHRASES: tuple[str, ...] = (
    "难吃", "不喜欢", "不想吃", "吃腻", "太咸", "太淡", "太辣", "太油", "太甜",
    "太贵", "太慢", "太久", "超时", "超支", "超预算", "太累", "换一个", "换掉",
    "不合适", "不满意", "失败", "没做完", "不好", "浪费", "剩下", "麻烦",
)
_TASTE_KEYWORDS: tuple[str, ...] = (
    "辣", "咸", "淡", "甜", "酸", "油腻", "清淡", "快手", "省时", "低糖", "低盐",
    "高蛋白", "儿童友好", "素食", "汤", "面", "米饭", "海鲜", "牛肉", "鸡肉",
    "猪肉", "豆制品", "蔬菜", "凉菜", "热菜", "早餐", "午餐", "晚餐",
)

_POSITIVE_RATING = 4
_NEGATIVE_RATING = 2
_VECTOR_WINDOW = 30


def classify_sentiment(content: str, rating: int | None = None) -> str:
    """判定反馈情感：评分优先，其次中文短语计票，都缺失时为中性。"""
    if rating is not None:
        if rating >= _POSITIVE_RATING:
            return "positive"
        if rating <= _NEGATIVE_RATING:
            return "negative"
        return "neutral"
    text = content.strip()
    if not text:
        return "neutral"
    positive = sum(phrase in text for phrase in _POSITIVE_PHRASES)
    negative = sum(phrase in text for phrase in _NEGATIVE_PHRASES)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def extract_taste_tags(*sources: str | Iterable[str]) -> tuple[str, ...]:
    """从自由文本与结构化标签中抽取口味标签，用于偏好学习。"""
    tags: list[str] = []
    for source in sources:
        if isinstance(source, str):
            tags.extend(keyword for keyword in _TASTE_KEYWORDS if keyword in source)
        else:
            tags.extend(str(item).strip() for item in source if str(item).strip())
    return tuple(dict.fromkeys(tag for tag in tags if tag))


@dataclass(frozen=True, slots=True)
class FeedbackSignal:
    """一次执行反馈的规范化描述，跨 PostgreSQL / Neo4j / Chroma 共用。"""

    user_id: int
    feedback_type: str
    subject: str = ""
    content: str = ""
    reference_type: str = ""
    reference_id: int = 0
    tags: tuple[str, ...] = ()
    rating: int | None = None
    planned_value: float = 0.0
    actual_value: float = 0.0
    sentiment: str = ""
    source: str = "auto"
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def deviation(self) -> float:
        return round(self.actual_value - self.planned_value, 2)

    @property
    def resolved_sentiment(self) -> str:
        return self.sentiment or classify_sentiment(self.content, self.rating)

    @property
    def label(self) -> str:
        return FEEDBACK_LABELS.get(self.feedback_type, self.feedback_type)

    def narrative(self) -> str:
        """生成可嵌入的自然语言描述——向量库里检索到的就是这段文字。"""
        marker = {"positive": "满意", "negative": "不满意", "neutral": "一般"}[
            self.resolved_sentiment
        ]
        parts = [f"[{self.label}] {self.subject or '未命名条目'}：反馈{marker}"]
        if self.rating is not None:
            parts.append(f"评分 {self.rating}/5")
        if self.planned_value or self.actual_value:
            parts.append(
                f"计划 {self.planned_value:g}、实际 {self.actual_value:g}、"
                f"偏差 {self.deviation:+g}"
            )
        if self.tags:
            parts.append(f"标签 {'、'.join(self.tags)}")
        if self.content:
            parts.append(f"原话“{self.content.strip()}”")
        parts.append(f"发生于 {self.occurred_at.strftime('%Y-%m-%d %H:%M')}")
        return "；".join(parts) + "。"


@dataclass(frozen=True, slots=True)
class FeedbackSyncResult:
    """回流结果，供接口返回与运维观测。"""

    feedback_id: int
    sentiment: str
    deviation: float
    graph_synced: bool
    vector_synced: bool
    notes: tuple[str, ...] = ()


class FeedbackLoopService:
    def __init__(self, knowledge: KnowledgeService | None = None) -> None:
        self._knowledge = knowledge

    @property
    def knowledge(self) -> KnowledgeService:
        # 延迟解析，避免导入期就构造 Chroma / Neo4j 客户端
        if self._knowledge is None:
            self._knowledge = get_knowledge_service()
        return self._knowledge

    async def capture(
        self, session: AsyncSession, signal: FeedbackSignal
    ) -> FeedbackSyncResult:
        """落库 + 回流。任何外部依赖异常都被吞掉，只反映在同步标记上。"""
        repository = FeedbackRepository(session)
        sentiment = signal.resolved_sentiment
        feedback = await repository.create(
            user_id=signal.user_id,
            feedback_type=signal.feedback_type,
            reference_type=signal.reference_type,
            reference_id=signal.reference_id,
            subject=signal.subject[:160],
            tags=list(signal.tags),
            rating=signal.rating,
            sentiment=sentiment,
            content=signal.content,
            planned_value=signal.planned_value,
            actual_value=signal.actual_value,
            deviation=signal.deviation,
            source=signal.source,
        )
        graph_ok, graph_note = await self._push_graph(signal, feedback.id)
        history = await repository.list_recent(
            signal.user_id,
            feedback_types=(signal.feedback_type,),
            limit=_VECTOR_WINDOW,
        )
        vector_ok, vector_note = await self._push_vector(signal, history)
        await repository.mark_synced(feedback, graph=graph_ok, vector=vector_ok)
        notes = tuple(note for note in (graph_note, vector_note) if note)
        return FeedbackSyncResult(
            feedback_id=feedback.id,
            sentiment=sentiment,
            deviation=signal.deviation,
            graph_synced=graph_ok,
            vector_synced=vector_ok,
            notes=notes,
        )

    async def replay(self, session: AsyncSession, row: PlanFeedback) -> FeedbackSyncResult:
        """重放一条已落库但未回流成功的反馈（Neo4j / Chroma 恢复后的补偿路径）。"""
        repository = FeedbackRepository(session)
        signal = _row_signal(row)
        graph_ok, graph_note = (
            (True, "")
            if row.synced_to_graph
            else await self._push_graph(signal, row.id)
        )
        vector_ok, vector_note = (True, "")
        if not row.synced_to_vector:
            history = await repository.list_recent(
                row.user_id, feedback_types=(row.feedback_type,), limit=_VECTOR_WINDOW
            )
            vector_ok, vector_note = await self._push_vector(signal, history)
        await repository.mark_synced(row, graph=graph_ok, vector=vector_ok)
        return FeedbackSyncResult(
            feedback_id=row.id,
            sentiment=row.sentiment,
            deviation=row.deviation,
            graph_synced=graph_ok,
            vector_synced=vector_ok,
            notes=tuple(note for note in (graph_note, vector_note) if note),
        )

    async def _push_graph(self, signal: FeedbackSignal, feedback_id: int) -> tuple[bool, str]:
        try:
            await self.knowledge.graph_store.sync_feedback_signal(
                signal.user_id,
                signal_key=f"{signal.feedback_type}:{feedback_id}",
                feedback_type=signal.feedback_type,
                sentiment=signal.resolved_sentiment,
                subject=signal.subject,
                content=signal.content or signal.narrative(),
                rating=signal.rating,
                deviation=signal.deviation,
                tags=signal.tags,
                occurred_at=signal.occurred_at.isoformat(),
            )
        except Exception as exc:  # noqa: BLE001 - 图谱不可用不阻断业务
            logger.warning("feedback graph sync failed: %s", exc)
            return False, f"图谱回流失败：{type(exc).__name__}"
        return True, ""

    async def _push_vector(
        self, signal: FeedbackSignal, history: Sequence[PlanFeedback]
    ) -> tuple[bool, str]:
        """按反馈类型维护一份滚动文档。

        固定 ``document_id`` 让每种反馈在知识库里只占一条记录，避免每次执行都
        新增文档把知识库刷屏；``replace_document`` 保证内容始终是最近
        ``_VECTOR_WINDOW`` 条的快照。
        """
        chunks = [signal.narrative(), *(_row_narrative(row) for row in history)]
        deduped = list(dict.fromkeys(chunk for chunk in chunks if chunk))[:_VECTOR_WINDOW]
        try:
            await self.knowledge.vector_store.replace_document(
                name=f"执行反馈·{signal.label}",
                category="执行反馈",
                chunks=deduped,
                user_id=signal.user_id,
                document_id=f"feedback-{signal.user_id}-{signal.feedback_type}",
            )
        except Exception as exc:  # noqa: BLE001 - 向量库不可用不阻断业务
            logger.warning("feedback vector sync failed: %s", exc)
            return False, f"向量回流失败：{type(exc).__name__}"
        return True, ""


def _row_signal(row: PlanFeedback) -> FeedbackSignal:
    """把已落库的反馈行还原成 :class:`FeedbackSignal`，供重放与文本生成复用。"""
    return FeedbackSignal(
        user_id=row.user_id,
        feedback_type=row.feedback_type,
        subject=row.subject,
        content=row.content,
        reference_type=row.reference_type,
        reference_id=row.reference_id,
        tags=tuple(row.tags),
        rating=row.rating,
        planned_value=row.planned_value,
        actual_value=row.actual_value,
        sentiment=row.sentiment,
        source=row.source,
        occurred_at=row.created_at or datetime.now(UTC),
    )


def _row_narrative(row: PlanFeedback) -> str:
    """把已落库的反馈行还原成同构的自然语言片段。"""
    return _row_signal(row).narrative()


feedback_loop_service = FeedbackLoopService()
