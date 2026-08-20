"""执行反馈仓储：``plan_feedback`` 偏差表的读写与口味画像聚合。

本模块只负责 MySQL 侧的持久化与统计；回流到 Neo4j / 向量库 的动作由
:mod:`app.services.feedback_loop` 编排，保持仓储层无外部依赖、可单测。
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlanFeedback, RecipeRecord

# 餐食类反馈的类型标签，口味画像只从这些记录中学习
MEAL_FEEDBACK_TYPES: tuple[str, ...] = ("meal_replacement", "meal_rating")


@dataclass(slots=True)
class TasteProfile:
    """口味画像：从历史反馈聚合出来的正/负向信号。

    直接注入餐食智能体的提示词，让 ``feedback`` 不再是一次性入参，而是可以
    跨会话累积的长期偏好记忆。
    """

    liked_tags: list[str] = field(default_factory=list)
    disliked_tags: list[str] = field(default_factory=list)
    liked_dishes: list[str] = field(default_factory=list)
    rejected_dishes: list[str] = field(default_factory=list)
    recent_notes: list[str] = field(default_factory=list)
    sample_size: int = 0

    @property
    def is_empty(self) -> bool:
        return not (
            self.liked_tags or self.disliked_tags or self.liked_dishes or self.rejected_dishes
        )

    def as_prompt_payload(self) -> dict[str, object]:
        """裁剪为提示词友好的紧凑结构，避免把整张反馈表塞进上下文。"""
        return {
            "liked_tags": self.liked_tags[:8],
            "disliked_tags": self.disliked_tags[:8],
            "liked_dishes": self.liked_dishes[:6],
            "rejected_dishes": self.rejected_dishes[:6],
            "recent_notes": self.recent_notes[:5],
            "sample_size": self.sample_size,
        }


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values: object) -> PlanFeedback:
        feedback = PlanFeedback(**values)
        self._session.add(feedback)
        await self._session.commit()
        await self._session.refresh(feedback)
        return feedback

    async def mark_synced(
        self, feedback: PlanFeedback, *, graph: bool, vector: bool
    ) -> PlanFeedback:
        feedback.synced_to_graph = graph
        feedback.synced_to_vector = vector
        await self._session.commit()
        await self._session.refresh(feedback)
        return feedback

    async def list_recent(
        self,
        user_id: int,
        *,
        feedback_types: Sequence[str] = (),
        limit: int = 30,
    ) -> list[PlanFeedback]:
        statement = select(PlanFeedback).where(PlanFeedback.user_id == user_id)
        if feedback_types:
            statement = statement.where(PlanFeedback.feedback_type.in_(list(feedback_types)))
        statement = statement.order_by(PlanFeedback.created_at.desc()).limit(limit)
        return list((await self._session.scalars(statement)).all())

    async def count_by_sentiment(self, user_id: int) -> dict[str, int]:
        statement = (
            select(PlanFeedback.sentiment, func.count(PlanFeedback.id))
            .where(PlanFeedback.user_id == user_id)
            .group_by(PlanFeedback.sentiment)
        )
        rows = (await self._session.execute(statement)).all()
        return {str(sentiment): int(count) for sentiment, count in rows}

    async def pending_sync(self, user_id: int, limit: int = 100) -> list[PlanFeedback]:
        """返回尚未回流到图谱或向量库的反馈，供补偿任务重放。"""
        statement = (
            select(PlanFeedback)
            .where(
                PlanFeedback.user_id == user_id,
                (PlanFeedback.synced_to_graph.is_(False))
                | (PlanFeedback.synced_to_vector.is_(False)),
            )
            .order_by(PlanFeedback.created_at)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def taste_profile(
        self,
        user_id: int,
        *,
        limit: int = 40,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> TasteProfile:
        """聚合餐食反馈 + 菜谱点赞，生成可直接喂给智能体的口味画像。

        ``since``/``until`` 限定反馈时间窗（阶段5 用于"本周 vs 上周"口味演化对比），
        缺省时取最近 ``limit`` 条。
        """
        statement = select(PlanFeedback).where(
            PlanFeedback.user_id == user_id,
            PlanFeedback.feedback_type.in_(list(MEAL_FEEDBACK_TYPES)),
        )
        if since is not None:
            statement = statement.where(PlanFeedback.created_at >= since)
        if until is not None:
            statement = statement.where(PlanFeedback.created_at < until)
        records = list(
            (
                await self._session.scalars(
                    statement.order_by(PlanFeedback.created_at.desc()).limit(limit)
                )
            ).all()
        )
        liked: Counter[str] = Counter()
        disliked: Counter[str] = Counter()
        liked_dishes: list[str] = []
        rejected_dishes: list[str] = []
        notes: list[str] = []
        for record in records:
            bucket = liked if record.sentiment == "positive" else disliked
            if record.sentiment != "neutral":
                bucket.update(tag for tag in record.tags if tag)
            if record.subject:
                target = liked_dishes if record.sentiment == "positive" else rejected_dishes
                if record.sentiment != "neutral" and record.subject not in target:
                    target.append(record.subject)
            if record.content:
                notes.append(record.content.strip()[:60])

        favorites = await self._session.scalars(
            select(RecipeRecord)
            .where(RecipeRecord.user_id == user_id, RecipeRecord.like_count > 0)
            .order_by(RecipeRecord.like_count.desc())
            .limit(6)
        )
        for recipe in favorites.all():
            liked.update({tag: recipe.like_count for tag in recipe.tags if tag})
            if recipe.name not in liked_dishes:
                liked_dishes.append(recipe.name)

        # 同一标签同时出现正负信号时，以净票数决定归属，避免自相矛盾的提示词
        net = {tag: liked[tag] - disliked[tag] for tag in {*liked, *disliked}}
        return TasteProfile(
            liked_tags=[tag for tag, score in sorted(net.items(), key=_by_score) if score > 0],
            disliked_tags=[tag for tag, score in sorted(net.items(), key=_by_score) if score < 0],
            liked_dishes=liked_dishes,
            rejected_dishes=rejected_dishes,
            recent_notes=notes,
            sample_size=len(records),
        )


def _by_score(item: tuple[str, int]) -> tuple[int, str]:
    return -abs(item[1]), item[0]
