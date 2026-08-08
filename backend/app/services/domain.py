import json
from collections import defaultdict
from collections.abc import Sequence

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import PlanMealItem, RecipeRecord
from app.repositories import DomainRepository, PlanningRepository
from app.repositories.feedback import FeedbackRepository, TasteProfile
from app.schemas.domain import BudgetAnalytics
from app.services.feedback_loop import (
    MEAL_REPLACEMENT,
    FeedbackSignal,
    FeedbackSyncResult,
    extract_taste_tags,
    feedback_loop_service,
)


class MealReplacementDraft(BaseModel):
    name: str
    duration: int = Field(ge=1, le=240)
    cost: float = Field(ge=0, le=100000)
    tags: list[str]
    reason: str
    ingredients: list[str]


class DomainOperationsService:
    """去家庭化版领域服务：单用户，无成员分配。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def replace_meal(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        meal_id: int,
        feedback: str,
        rating: int | None = None,
        tags: Sequence[str] = (),
    ) -> tuple[PlanMealItem | None, FeedbackSyncResult | None]:
        """按反馈替换餐食，并把这条反馈沉淀为可复用的口味偏好。"""
        planning = PlanningRepository(session)
        meal = await planning.get_meal_item(meal_id, user_id)
        if meal is None:
            return None, None
        repository = DomainRepository(session)
        feedback_repository = FeedbackRepository(session)
        recipes = await repository.list_recipes(user_id)
        profile = await feedback_repository.taste_profile(user_id)
        fallback = self._replacement_fallback(meal, feedback, recipes, profile)
        draft = fallback
        if self._settings.real_llm_enabled:
            draft = await self._generate_replacement(
                meal, feedback, recipes, fallback, profile
            )
        rejected_name = meal.name
        rejected_tags = list(meal.tags)
        item = await repository.replace_meal(meal, **draft.model_dump())
        await self._reinforce_recipe(repository, recipes, draft.name)
        sync = await feedback_loop_service.capture(
            session,
            FeedbackSignal(
                user_id=user_id,
                feedback_type=MEAL_REPLACEMENT,
                subject=rejected_name,
                content=feedback,
                reference_type="plan_meal_item",
                reference_id=meal_id,
                tags=extract_taste_tags(feedback, tags, rejected_tags),
                rating=rating,
                sentiment="" if rating is not None else "negative",
                source="user",
            ),
        )
        return item, sync

    @staticmethod
    async def _reinforce_recipe(
        repository: DomainRepository,
        recipes: Sequence[RecipeRecord],
        chosen_name: str,
    ) -> None:
        """替换命中菜谱时给它 +1 票，让下次候选排序更贴近真实口味。"""
        match = next((item for item in recipes if item.name == chosen_name), None)
        if match is not None:
            await repository.save_recipe(match, like_count=match.like_count + 1)

    async def budget_analytics(self, session: AsyncSession, user_id: int) -> BudgetAnalytics:
        repository = DomainRepository(session)
        expenses = await repository.list_expenses(user_id)
        plan = await PlanningRepository(session).get_active_plan(user_id)
        limit = plan.budget if plan is not None else 500
        actual = round(sum(item.amount for item in expenses), 2)
        by_category: dict[str, float] = defaultdict(float)
        monthly: dict[str, float] = defaultdict(float)
        for item in expenses:
            by_category[item.category] += item.amount
            monthly[item.occurred_at.strftime("%Y-%m")] += item.amount
        usage = round(actual / limit * 100) if limit else 0
        return BudgetAnalytics(
            limit=limit,
            actual_spent=actual,
            remaining=round(limit - actual, 2),
            usage_percent=usage,
            warning=usage >= 85,
            by_category={key: round(value, 2) for key, value in by_category.items()},
            monthly_trend={key: round(value, 2) for key, value in sorted(monthly.items())},
        )

    async def _generate_replacement(
        self,
        meal: PlanMealItem,
        feedback: str,
        recipes: list[RecipeRecord],
        fallback: MealReplacementDraft,
        profile: TasteProfile | None = None,
    ) -> MealReplacementDraft:
        model = ChatOpenAI(
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
            model=self._settings.llm_model,
            temperature=0.2,
            timeout=self._settings.llm_timeout_seconds,
            max_retries=1,
            max_tokens=800,
        ).bind(response_format={"type": "json_object"})
        payload = {
            "current_meal": {
                "name": meal.name,
                "duration": meal.duration,
                "cost": meal.cost,
                "ingredients": meal.ingredients,
            },
            "feedback": feedback,
            "taste_profile": profile.as_prompt_payload() if profile else {},
            "recipes": [
                {
                    "name": item.name,
                    "ingredients": item.ingredients,
                    "duration": item.duration,
                    "estimated_cost": item.estimated_cost,
                    "like_count": item.like_count,
                }
                for item in recipes[:20]
            ],
            "schema": MealReplacementDraft.model_json_schema(),
        }
        try:
            response = await model.ainvoke(
                [
                    (
                        "system",
                        "你是餐食替换智能体，只输出符合 Schema 的 JSON。"
                        "taste_profile.disliked_tags 与 rejected_dishes 是历史负反馈，"
                        "替换结果必须回避；liked_tags 与 like_count 高的菜谱优先选用。",
                    ),
                    ("user", json.dumps(payload, ensure_ascii=False)),
                ]
            )
            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )
            return MealReplacementDraft.model_validate_json(content)
        except Exception:
            return fallback

    @staticmethod
    def _replacement_fallback(
        meal: PlanMealItem,
        feedback: str,
        recipes: list[RecipeRecord],
        profile: TasteProfile | None = None,
    ) -> MealReplacementDraft:
        """无 LLM 时的确定性替换，完全由历史反馈驱动。"""
        disliked = set(profile.disliked_tags) if profile else set()
        rejected = {*(profile.rejected_dishes if profile else ()), meal.name}
        liked = set(profile.liked_tags) if profile else set()
        candidates = [
            recipe
            for recipe in recipes
            if recipe.name not in rejected and not (set(recipe.tags) & disliked)
        ]
        if candidates:
            recipe = max(
                candidates,
                key=lambda item: (
                    len(set(item.tags) & liked),
                    item.like_count,
                    item.is_favorite,
                ),
            )
            hit = sorted(set(recipe.tags) & liked)
            learned = f"，命中历史偏好 {'、'.join(hit)}" if hit else ""
            return MealReplacementDraft(
                name=recipe.name,
                duration=recipe.duration,
                cost=recipe.estimated_cost,
                tags=list(recipe.tags),
                reason=f"根据反馈“{feedback}”从菜谱替换{learned}",
                ingredients=list(recipe.ingredients),
            )
        quick = "快手" in feedback or "简单" in feedback or "快手" in liked
        return MealReplacementDraft(
            name="番茄鸡蛋面" if quick else "菌菇鸡肉焖饭",
            duration=18 if quick else 35,
            cost=22 if quick else 38,
            tags=["反馈替换", "快手" if quick else "一锅料理"],
            reason=f"根据反馈“{feedback}”替换原餐食 {meal.name}",
            ingredients=["番茄", "鸡蛋", "面条"] if quick else ["鸡肉", "菌菇", "大米"],
        )

    # Phase 3 清理：任务排程（auto_assign_tasks / _next_available_slot）已移除，
    # plan_tasks 表删除后不再有可排程的任务实体。餐食替换与预算分析仍保留。


domain_operations_service = DomainOperationsService()
