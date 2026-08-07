"""营养目标求解服务（去家庭化版）。

SoloChef 的营养目标来源于 :class:`~app.models.NutritionGoal`（TDEE + 宏量分配），
本服务聚合活跃计划的餐食营养，并对比目标输出达成报告。营养数据来源于用户菜谱
``RecipeRecord.nutrition``（每份 JSON），餐食按名称匹配菜谱；无匹配时按食材
数量给出保守估算并标注"未校准"。
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models import PlanMealItem, RecipeRecord
from app.schemas.domain import NutrientEntry, NutritionReport

# 兜底营养目标（无 NutritionGoal 时使用成年人基准）
_DEFAULT_TARGETS: dict[str, float] = {
    "calories": 2200,
    "protein_g": 65,
    "fat_g": 70,
    "carbs_g": 300,
}

# 每种食材的粗略营养贡献（每 100g 估算），用于无菜谱匹配时的保守估算
_INGREDIENT_NUTRITION: dict[str, dict[str, float]] = {
    "鸡肉": {"calories": 165, "protein_g": 31, "fat_g": 3.6, "carbs_g": 0},
    "猪肉": {"calories": 242, "protein_g": 27, "fat_g": 14, "carbs_g": 0},
    "牛肉": {"calories": 250, "protein_g": 26, "fat_g": 15, "carbs_g": 0},
    "鸡蛋": {"calories": 155, "protein_g": 13, "fat_g": 11, "carbs_g": 1.1},
    "番茄": {"calories": 18, "protein_g": 0.9, "fat_g": 0.2, "carbs_g": 3.9},
    "青菜": {"calories": 15, "protein_g": 1.5, "fat_g": 0.2, "carbs_g": 2.0},
    "大米": {"calories": 130, "protein_g": 2.7, "fat_g": 0.3, "carbs_g": 28},
    "面条": {"calories": 110, "protein_g": 3.5, "fat_g": 0.6, "carbs_g": 22},
    "豆腐": {"calories": 76, "protein_g": 8, "fat_g": 4.8, "carbs_g": 1.9},
    "鱼": {"calories": 145, "protein_g": 23, "fat_g": 5, "carbs_g": 0},
}


def _match_recipe(meal: PlanMealItem, recipes: Sequence[RecipeRecord]) -> RecipeRecord | None:
    """按名称模糊匹配用户菜谱。"""
    name = meal.name.strip()
    for recipe in recipes:
        if recipe.name == name or name in recipe.name or recipe.name in name:
            return recipe
    return None


def estimate_meal_nutrition(
    meal: PlanMealItem, recipes: Sequence[RecipeRecord]
) -> tuple[dict[str, float], bool]:
    """估算单餐营养，返回 (营养字典, 是否命中菜谱校准)。"""
    recipe = _match_recipe(meal, recipes)
    if recipe is not None and recipe.nutrition:
        servings = recipe.servings or 2
        scale = 1.0 / servings  # 单份
        return {key: round(value * scale, 1) for key, value in recipe.nutrition.items()}, True
    estimate: dict[str, float] = {}
    for ingredient in meal.ingredients:
        for keyword, nutrition in _INGREDIENT_NUTRITION.items():
            if keyword in ingredient:
                for key, value in nutrition.items():
                    estimate[key] = estimate.get(key, 0.0) + value * 0.5
                break
    return {key: round(value, 1) for key, value in estimate.items()}, False


def build_nutrition_report(
    meals: Sequence[PlanMealItem],
    recipes: Sequence[RecipeRecord],
    targets: dict[str, float] | None = None,
) -> NutritionReport:
    """构建营养目标达成报告。

    ``targets`` 来自 :class:`~app.models.NutritionGoal`（TDEE + 宏量分配）；
    为空时使用成年人兜底基准。
    """
    resolved_targets = dict(targets) if targets else dict(_DEFAULT_TARGETS)
    actual: dict[str, float] = {}
    calibrated = 0
    uncalibrated = 0
    for meal in meals:
        nutrition, is_calibrated = estimate_meal_nutrition(meal, recipes)
        if is_calibrated:
            calibrated += 1
        else:
            uncalibrated += 1
        for key, value in nutrition.items():
            actual[key] = actual.get(key, 0.0) + value

    nutrients: dict[str, NutrientEntry] = {}
    percents: list[float] = []
    for key, target_value in resolved_targets.items():
        actual_value = round(actual.get(key, 0.0), 1)
        percent = round(actual_value / target_value * 100, 1) if target_value else 0.0
        percent = min(percent, 200.0)
        satisfied = percent >= 90.0
        nutrients[key] = NutrientEntry(
            target=round(target_value, 1),
            actual=actual_value,
            percent=percent,
            satisfied=satisfied,
        )
        percents.append(percent)

    overall_percent = round(sum(percents) / len(percents), 1) if percents else 0.0
    return NutritionReport(
        targets=resolved_targets,
        actual={key: round(value, 1) for key, value in actual.items()},
        nutrients=nutrients,
        overall_percent=overall_percent,
        satisfied=overall_percent >= 90.0,
        calibrated_meals=calibrated,
        uncalibrated_meals=uncalibrated,
        member_count=1,
        meal_count=len(meals),
    )
