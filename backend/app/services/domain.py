
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models import PlanMealItem, RecipeRecord
from app.repositories import DomainRepository, PlanningRepository
from app.repositories.feedback import FeedbackRepository, TasteProfile
from app.schemas.domain import BudgetAnalytics, ShoppingItem
from app.services.feedback_loop import (
    MEAL_REPLACEMENT,
    FeedbackSignal,
    FeedbackSyncResult,
    extract_taste_tags,
    feedback_loop_service,
)
from app.services.nutrition import (
    estimate_meal_nutrition,
    sum_meal_nutrition,
)
from app.services.shopping_categories import normalize_shopping_category

# 食材 → 采购分类的启发式映射，仅用于餐食替换联动生成购物项时的兜底归类。
_INGREDIENT_CATEGORY: dict[str, list[str]] = {
    "肉蛋奶": [
        "鸡", "猪", "牛", "羊", "鱼", "虾", "蟹", "蛤", "扇贝", "鱿鱼",
        "豆腐", "豆浆", "牛奶", "酸奶", "奶酪", "蛋",
    ],
    "蔬菜": [
        "番茄", "黄瓜", "青菜", "白菜", "菠菜", "芹菜", "生菜", "西兰花", "花菜",
        "胡萝卜", "萝卜", "洋葱", "茄子", "青椒", "辣椒", "蘑菇", "香菇", "金针菇",
        "木耳", "豆芽", "豌豆", "毛豆", "玉米", "莲藕", "冬瓜", "丝瓜", "苦瓜",
        "豆角", "四季豆", "蒜苔", "韭菜", "葱", "姜", "蒜", "南瓜", "土豆",
        "山药", "芋头", "红薯", "紫薯",
    ],
    "主食": ["米", "饭", "面", "馒头", "包子", "饺子", "面包", "燕麦", "小米"],
    "水果": ["苹果", "香蕉", "橙", "柑", "橘", "柚", "葡萄", "草莓", "蓝莓", "桃", "梨", "猕猴桃", "芒果", "菠萝", "西瓜", "哈密瓜", "樱桃"],
}
_INGREDIENT_CATEGORY_ALIASES: dict[str, str] = {"西红柿": "番茄", "马铃薯": "土豆"}


def _category_for_ingredient(name: str) -> str:
    """按关键词命中采购分类；未命中统一归入“其他”。"""
    normalized = _INGREDIENT_CATEGORY_ALIASES.get(name, name)
    for category, keywords in _INGREDIENT_CATEGORY.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return normalize_shopping_category("其他", name)


def _normalize_shopping_name(name: str) -> str:
    """与 ``DomainRepository.merge_shopping`` 对齐的归一化，用于去重判断。"""
    normalized = "".join(name.lower().split())
    return _INGREDIENT_CATEGORY_ALIASES.get(normalized, normalized)


@dataclass
class MealReplacementOutcome:
    """餐食替换的完整结果，供接口层组装为 :class:`MealReplacementResponse`。"""

    meal: PlanMealItem
    feedback: FeedbackSyncResult | None
    meal_nutrition_before: dict[str, float]
    meal_nutrition_after: dict[str, float]
    meal_calibrated_before: bool
    meal_calibrated_after: bool
    day: str
    day_nutrition_before: dict[str, float]
    day_nutrition_after: dict[str, float]
    shopping_added: list[ShoppingItem]
    shopping_removed: list[ShoppingItem]
    shopping_merged_groups: int
    shopping_removed_duplicates: int


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
    ) -> MealReplacementOutcome | None:
        """按反馈替换餐食，并把这条反馈沉淀为可复用的口味偏好。

        替换后额外完成两件事（执行反馈闭环的"营养联动"补完）：
        1. 重算单餐与当天营养差异（命中菜谱校准时标注 ``calibrated``）；
        2. 联动购物清单——移除上次替换为本餐生成的条目，按新食材补回，
           并触发一次合并去重。
        """
        planning = PlanningRepository(session)
        meal = await planning.get_meal_item(meal_id, user_id)
        if meal is None:
            return None
        repository = DomainRepository(session)
        feedback_repository = FeedbackRepository(session)
        recipes = await repository.list_recipes(user_id)
        profile = await feedback_repository.taste_profile(user_id)

        # 替换前快照：单餐营养 + 当天（含本餐旧值）合计
        plan = await planning.get_active_plan(user_id)
        if plan is None:
            return None
        day_meals = [item for item in plan.meals if item.day == meal.day]
        old_nutrition, old_calib = estimate_meal_nutrition(meal, recipes)
        before_day = sum_meal_nutrition(
            day_meals, recipes, override_id=meal.id, override_nutrition=old_nutrition
        )

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

        # 替换后：单餐营养 + 当天（含本餐新值）合计
        new_nutrition, new_calib = estimate_meal_nutrition(item, recipes)
        after_day = sum_meal_nutrition(
            day_meals, recipes, override_id=item.id, override_nutrition=new_nutrition
        )

        # 购物清单联动：清理上次为本餐生成的条目，按新食材补回并合并
        source_tag = f"餐食:{meal.id}"
        removed_orm = await repository.remove_shopping_by_source(user_id, source_tag)
        shopping_removed = [
            ShoppingItem.model_validate(entry, from_attributes=True) for entry in removed_orm
        ]
        # 删除已提交，刷新集合以反映最新状态，避免重复补回其他餐食共享的食材
        await session.refresh(plan, ["shopping_items"])
        existing_names = {
            _normalize_shopping_name(entry.name) for entry in plan.shopping_items
        }
        for ingredient in draft.ingredients:
            if _normalize_shopping_name(ingredient) in existing_names:
                continue
            await repository.add_shopping_item(
                user_id,
                name=ingredient,
                category=_category_for_ingredient(ingredient),
                quantity="1 份",
                source=source_tag,
            )
            existing_names.add(_normalize_shopping_name(ingredient))
        merged_groups, removed_duplicates, _, _ = await repository.merge_shopping(user_id)
        # merge 返回的购物项集合可能仍是会话内的旧快照，强制刷新后取本餐打标的条目
        await session.refresh(plan, ["shopping_items"])
        shopping_added = [
            ShoppingItem.model_validate(entry, from_attributes=True)
            for entry in plan.shopping_items
            if entry.source == source_tag
        ]

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
        return MealReplacementOutcome(
            meal=item,
            feedback=sync,
            meal_nutrition_before=old_nutrition,
            meal_nutrition_after=new_nutrition,
            meal_calibrated_before=old_calib,
            meal_calibrated_after=new_calib,
            day=meal.day,
            day_nutrition_before=before_day,
            day_nutrition_after=after_day,
            shopping_added=shopping_added,
            shopping_removed=shopping_removed,
            shopping_merged_groups=merged_groups,
            shopping_removed_duplicates=removed_duplicates,
        )

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
