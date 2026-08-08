"""营养目标求解服务（去家庭化版）。

SoloChef 的营养目标来源于 :class:`~app.models.NutritionGoal`（TDEE + 宏量分配），
本服务聚合活跃计划的餐食营养，并对比目标输出达成报告。营养数据来源于用户菜谱
``RecipeRecord.nutrition``（每份 JSON），餐食按名称匹配菜谱；无匹配时按食材
数量给出保守估算并标注"未校准"。

食材营养库（每 100g 可食部）外置于 ``app/data/ingredient_nutrition.json``，
由 :func:`app.data.load_ingredient_nutrition` 惰性加载并进程内缓存。每条记录
携带 ``calibration`` 字段（``verified`` / ``estimated``），便于在报告里区分
"已对照成分表校准"与"经验估算待校准"的食材。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.data import load_ingredient_nutrition
from app.models import NutritionGoal, RecipeRecord, UserProfile
from app.schemas.domain import NutrientEntry, NutritionReport


class MealLike(Protocol):
    """可被营养估算的餐食对象（ORM 模型或 Pydantic schema 均可）。"""

    name: str
    ingredients: list[str]

# 兜底营养目标（无 NutritionGoal 时使用成年人基准）
_DEFAULT_TARGETS: dict[str, float] = {
    "calories": 2200,
    "protein_g": 65,
    "fat_g": 70,
    "carbs_g": 300,
}

# Mifflin-St Jeor 基础代谢公式中的性别常数
_BMR_GENDER_OFFSET: dict[str, float] = {"male": 5.0, "female": -161.0}

# 活动系数（TDEE = BMR × 活动系数）
_ACTIVITY_FACTORS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
}

# 目标类型对应的热量调整系数与宏量分配比例
_GOAL_ADJUSTMENT: dict[str, float] = {
    "bulk": 1.10,      # 增肌：热量盈余 10%
    "cut": 0.85,       # 减脂：热量赤字 15%
    "maintain": 1.0,   # 维护：不调整
}

# 宏量营养素分配比例（蛋白质/碳水/脂肪占总热量的百分比）
_MACRO_RATIOS: dict[str, tuple[float, float, float]] = {
    "bulk": (0.30, 0.40, 0.30),      # 增肌：P30% C40% F30%
    "cut": (0.40, 0.30, 0.30),       # 减脂：P40% C30% F30%
    "maintain": (0.25, 0.50, 0.25),  # 维护：P25% C50% F25%
}

# 营养素热量系数（1g 蛋白质/碳水 = 4 kcal，1g 脂肪 = 9 kcal）
_CALORIES_PER_GRAM_PROTEIN = 4.0
_CALORIES_PER_GRAM_CARB = 4.0
_CALORIES_PER_GRAM_FAT = 9.0

# TDEE 钳制区间，防止极端身体数据产生异常结果
_TDEE_MIN = 1000.0
_TDEE_MAX = 5000.0


def compute_nutrition_goal(profile: UserProfile) -> NutritionGoal:
    """根据用户画像按 Mifflin-St Jeor 公式计算营养目标。

    计算链路：
        BMR = 10×weight + 6.25×height - 5×age + 性别常数
        TDEE = BMR × 活动系数（钳制到 [1000, 5000] kcal）
        target_calories = TDEE × 目标调整系数
        宏量分配 = target_calories × 比例 ÷ 热量系数

    Args:
        profile: 用户画像，含身高/体重/年龄/性别/活动水平/目标类型。

    Returns:
        未持久化的 :class:`NutritionGoal` 实例（调用方负责 ``session.add`` + ``commit``）。
    """
    gender_offset = _BMR_GENDER_OFFSET.get(profile.gender, 5.0)
    bmr = (
        10.0 * profile.weight_kg
        + 6.25 * profile.height_cm
        - 5.0 * profile.age
        + gender_offset
    )

    activity_factor = _ACTIVITY_FACTORS.get(profile.activity_level, 1.55)
    tdee = bmr * activity_factor
    tdee = max(_TDEE_MIN, min(_TDEE_MAX, tdee))

    adjustment = _GOAL_ADJUSTMENT.get(profile.goal_type, 1.0)
    target_calories = round(tdee * adjustment, 1)

    protein_ratio, carb_ratio, fat_ratio = _MACRO_RATIOS.get(
        profile.goal_type, _MACRO_RATIOS["maintain"]
    )
    protein_g = round(target_calories * protein_ratio / _CALORIES_PER_GRAM_PROTEIN, 1)
    carb_g = round(target_calories * carb_ratio / _CALORIES_PER_GRAM_CARB, 1)
    fat_g = round(target_calories * fat_ratio / _CALORIES_PER_GRAM_FAT, 1)

    return NutritionGoal(
        user_id=profile.user_id,
        goal_type=profile.goal_type,
        bmr=round(bmr, 1),
        tdee=round(tdee, 1),
        target_calories=target_calories,
        protein_g=protein_g,
        carb_g=carb_g,
        fat_g=fat_g,
        activity_level=profile.activity_level,
    )


def nutrition_goal_to_targets(goal: NutritionGoal) -> dict[str, float]:
    """将 :class:`NutritionGoal` 转为营养报告使用的 targets 字典。"""
    return {
        "calories": goal.target_calories,
        "protein_g": goal.protein_g,
        "fat_g": goal.fat_g,
        "carbs_g": goal.carb_g,
    }

# 食材营养库（每 100g 可食部）外置于 app/data/ingredient_nutrition.json，
# 由 load_ingredient_nutrition() 惰性加载并 lru_cache 缓存。Phase 3 扩充至 100+ 种。
# 每条记录含 calories/protein_g/fat_g/carbs_g/default_portion_g/calibration。
_INGREDIENT_NUTRITION = load_ingredient_nutrition()


def _match_recipe(meal: MealLike, recipes: Sequence[RecipeRecord]) -> RecipeRecord | None:
    """按名称模糊匹配用户菜谱。"""
    name = meal.name.strip()
    for recipe in recipes:
        if recipe.name == name or name in recipe.name or recipe.name in name:
            return recipe
    return None


def _ingredient_nutrition_for(name: str) -> tuple[dict[str, float], bool]:
    """按食材名匹配营养库，返回 (营养贡献, 是否命中)。

    命中时按 ``default_portion_g`` 折算到单次用量（每 100g × 用量/100）；
    未命中时跳过该食材（贡献为空字典）。
    """
    for keyword, entry in _INGREDIENT_NUTRITION.items():
        if keyword in name:
            portion = entry.get("default_portion_g", 100)
            scale = portion / 100.0
            return {
                "calories": entry["calories"] * scale,
                "protein_g": entry["protein_g"] * scale,
                "fat_g": entry["fat_g"] * scale,
                "carbs_g": entry["carbs_g"] * scale,
            }, True
    return {}, False


def estimate_meal_nutrition(
    meal: MealLike, recipes: Sequence[RecipeRecord]
) -> tuple[dict[str, float], bool]:
    """估算单餐营养，返回 (营养字典, 是否命中菜谱校准)。

    优先用菜谱 ``RecipeRecord.nutrition``（已校准的每份营养）；无匹配菜谱时
    退回食材营养库累加——命中食材库的营养按各食材 ``default_portion_g`` 折算，
    此时 ``is_calibrated`` 返回 False 以提示"未命中菜谱校准"。
    """
    recipe = _match_recipe(meal, recipes)
    if recipe is not None and recipe.nutrition:
        servings = recipe.servings or 2
        scale = 1.0 / servings  # 单份
        return {key: round(value * scale, 1) for key, value in recipe.nutrition.items()}, True
    estimate: dict[str, float] = {}
    for ingredient in meal.ingredients:
        nutrition, hit = _ingredient_nutrition_for(ingredient)
        if not hit:
            continue
        for key, value in nutrition.items():
            estimate[key] = estimate.get(key, 0.0) + value
    return {key: round(value, 1) for key, value in estimate.items()}, False


def build_nutrition_report(
    meals: Sequence[MealLike],
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
