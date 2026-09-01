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
import re
from typing import Protocol

from app.data import load_ingredient_nutrition
from app.models import NutritionGoal, RecipeRecord, UserProfile
from app.schemas.domain import NutrientEntry, NutritionReport


class MealLike(Protocol):
    """可被营养估算的餐食对象（ORM 模型或 Pydantic schema 均可）。"""

    name: str
    ingredients: list[str]

# 兜底营养目标（无 NutritionGoal 时使用成年人基准）
# 基于中国 DRIs 2023：成年男性轻体力活动 EER 约 2250 kcal，此处取 2200
# 蛋白质 65g = 65kg × 1.0 g/kg（DRIs RNI）；脂肪/碳水按 AMDR
_DEFAULT_TARGETS: dict[str, float] = {
    "calories": 2200,
    "protein_g": 65.0,   # 65kg × 1.0 g/kg（DRIs RNI）
    "fat_g": 61.1,       # 2200 × 25% ÷ 9（AMDR 中值）
    "carbs_g": 348.0,    # 补足剩余热量 ÷ 4
}

# Mifflin-St Jeor 基础代谢公式中的性别常数
_BMR_GENDER_OFFSET: dict[str, float] = {"male": 5.0, "female": -161.0}

# 身体活动水平（PAL）— 中国居民膳食营养素参考摄入量 2023版
# DRIs 定义三级：轻 1.50 / 中 1.75 / 重 2.00
# 项目四级映射：sedentary 略低于 DRIs 轻度下限
_ACTIVITY_FACTORS: dict[str, float] = {
    "sedentary": 1.40,   # 久坐少动（低于 DRIs 轻度）
    "light": 1.50,       # 轻身体活动（DRIs 轻度）
    "moderate": 1.75,    # 中等身体活动（DRIs 中度）
    "active": 2.00,      # 重身体活动（DRIs 重度）
}

# 目标类型对应的热量调整系数
# 注意：热量盈余/赤字为健身实践，非 DRIs 官方推荐
_GOAL_ADJUSTMENT: dict[str, float] = {
    "bulk": 1.10,      # 增肌：热量盈余 10%
    "cut": 0.85,       # 减脂：热量赤字 15%
    "maintain": 1.0,   # 维护：不调整
}

# 蛋白质系数范围（g/kg 体重/天），min/max 双值
# 基线参考《中国居民膳食营养素参考摄入量 2023版》RNI（1.0 g/kg）；
# 运动增量参考 ISSN 运动营养立场声明（Jäger et al., 2017）：
#   休闲运动者 1.1-1.6 / 耐力运动员 1.0-1.6 / 力量运动员 1.4-2.0
# 热量赤字（减脂）额外 +0.3-0.4 g/kg 以保肌肉（ISSN 2021 补充立场）
# 三维表：[goal_type][activity_level] → (min, max) g/kg
_PROTEIN_PER_KG_RANGE: dict[str, dict[str, tuple[float, float]]] = {
    "maintain": {
        "sedentary": (0.8, 1.2),
        "light":     (1.0, 1.4),
        "moderate":  (1.1, 1.5),
        "active":    (1.2, 1.6),
    },
    "cut": {
        "sedentary": (1.0, 1.4),
        "light":     (1.2, 1.6),
        "moderate":  (1.3, 1.7),
        "active":    (1.4, 1.8),
    },
    "bulk": {
        "sedentary": (1.2, 1.6),
        "light":     (1.4, 1.8),
        "moderate":  (1.5, 1.9),
        "active":    (1.6, 2.0),
    },
}

# 蛋白质供能比安全上限（防止大体重用户蛋白质热量占比过高）
_PROTEIN_CALORIE_CAP = 0.30

# 脂肪供能比 — 中国居民膳食营养素参考摄入量 2023版 AMDR 中值（范围 20-30%）
_FAT_RATIO = 0.25

# 营养素热量系数（1g 蛋白质/碳水 = 4 kcal，1g 脂肪 = 9 kcal）
_CALORIES_PER_GRAM_PROTEIN = 4.0
_CALORIES_PER_GRAM_CARB = 4.0
_CALORIES_PER_GRAM_FAT = 9.0

# TDEE 钳制区间，防止极端身体数据产生异常结果
_TDEE_MIN = 1000.0
_TDEE_MAX = 5000.0

# 等价物换算基准（用于生成"相当于 X"的直观解释）
# 数值取自《中国食物成分表》常见份量，帮用户建立感知，非精确营养计算。
# 鸡胸肉按 20g 蛋白 / 100g 锚定（验收标准）。
_CHICKEN_PROTEIN_PER_PIECE = 30.0   # 一块鸡胸肉（约 150g）≈ 30g 蛋白（20g/100g × 1.5）
_EGG_PROTEIN = 7.0                  # 一个鸡蛋 ≈ 7g 蛋白
_RICE_BOWL_KCAL = 200.0             # 一碗米饭（约 150g 熟饭）≈ 200 kcal
_RICE_BOWL_CARB = 40.0              # 一碗米饭 ≈ 40g 碳水
_BREAD_SLICE_CARB = 15.0            # 一片面包 ≈ 15g 碳水
_OIL_TBSP_FAT = 10.0                # 一汤匙食用油 ≈ 10g 脂肪
_NUTS_HANDFUL_FAT = 12.0            # 一小把坚果（约 25g）≈ 12g 脂肪


def compute_nutrition_goal(profile: UserProfile) -> NutritionGoal:
    """根据用户画像计算营养目标。

    采用范围设计（非单一固定值）：
      - 能量：BMR（Mifflin-St Jeor）× PAL（DRIs 2023）× 目标调整系数 ± 7%
      - 蛋白质：体重 × g/kg 系数区间（ISSN 立场声明）
      - 脂肪：剩余热量 × 供能比区间（20%-30%）÷ 9
      - 碳水：剩余热量 × 供能比区间（45%-65%）÷ 4

    蛋白质供能比超过 30% 时自动钳制，防止碳水占比过低。

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

    activity_factor = _ACTIVITY_FACTORS.get(profile.activity_level, 1.75)
    tdee = bmr * activity_factor
    tdee = max(_TDEE_MIN, min(_TDEE_MAX, tdee))

    adjustment = _GOAL_ADJUSTMENT.get(profile.goal_type, 1.0)
    target_calories = round(tdee * adjustment, 1)

    # 热量范围：目标值 ± 7%
    calories_min = round(target_calories * 0.93, 1)
    calories_max = round(target_calories * 1.07, 1)

    # 蛋白质范围：体重 × g/kg 区间（DRIs RNI + ISSN 运动增量）
    goal_ranges = _PROTEIN_PER_KG_RANGE.get(
        profile.goal_type, _PROTEIN_PER_KG_RANGE["maintain"]
    )
    per_kg_min, per_kg_max = goal_ranges.get(profile.activity_level, (0.8, 1.2))
    protein_min = round(profile.weight_kg * per_kg_min, 1)
    protein_max = round(profile.weight_kg * per_kg_max, 1)
    protein_g = round((protein_min + protein_max) / 2, 1)

    # 安全钳制：蛋白质供能比不超过 30%，防止大体重用户碳水占比过低
    protein_calorie_cap = target_calories * _PROTEIN_CALORIE_CAP
    max_protein_allowed = protein_calorie_cap / _CALORIES_PER_GRAM_PROTEIN
    if protein_max > max_protein_allowed:
        protein_max = round(max_protein_allowed, 1)
        protein_min = min(protein_min, protein_max)
        protein_g = round((protein_min + protein_max) / 2, 1)

    # 脂肪范围：剩余热量 × 供能比区间（20%-30%）÷ 9
    remaining_for_fat = target_calories - protein_g * _CALORIES_PER_GRAM_PROTEIN
    fat_min = round(remaining_for_fat * 0.20 / _CALORIES_PER_GRAM_FAT, 1)
    fat_max = round(remaining_for_fat * 0.30 / _CALORIES_PER_GRAM_FAT, 1)
    fat_g = round((fat_min + fat_max) / 2, 1)

    # 碳水范围：剩余热量 × 供能比区间（45%-65%）÷ 4
    remaining_for_carbs = (
        target_calories
        - protein_g * _CALORIES_PER_GRAM_PROTEIN
        - fat_g * _CALORIES_PER_GRAM_FAT
    )
    carb_min = round(max(0.0, remaining_for_carbs * 0.45) / _CALORIES_PER_GRAM_CARB, 1)
    carb_max = round(max(0.0, remaining_for_carbs * 0.65) / _CALORIES_PER_GRAM_CARB, 1)
    carb_g = round((carb_min + carb_max) / 2, 1)

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
        calories_min=calories_min,
        calories_max=calories_max,
        protein_min=protein_min,
        protein_max=protein_max,
        carb_min=carb_min,
        carb_max=carb_max,
        fat_min=fat_min,
        fat_max=fat_max,
    )


def nutrition_goal_to_targets(goal: NutritionGoal) -> dict[str, float]:
    """将 :class:`NutritionGoal` 转为营养报告使用的 targets 字典。"""
    return {
        "calories": goal.target_calories,
        "protein_g": goal.protein_g,
        "fat_g": goal.fat_g,
        "carbs_g": goal.carb_g,
    }


def _portion_range(lo: float, hi: float, per_unit: float) -> str:
    """把 (lo, hi) 克数换算成"份量区间"字符串，四舍五入。

    例如 ``(65, 85)`` 除以 ``30`` → ``"2~3"``；上下限四舍五入后相同则返回
    单个数字。四舍五入向上取整（``int(x + 0.5)``），与前端 ``Math.round``
    语义保持一致，避免"2.17 块"这类反直觉表述。
    """
    lo_n = int(lo / per_unit + 0.5)
    hi_n = int(hi / per_unit + 0.5)
    if lo_n == hi_n:
        return str(lo_n)
    return f"{lo_n}~{hi_n}"


def build_nutrition_hints(goal: NutritionGoal) -> dict[str, str]:
    """为营养目标生成"相当于 X"的直观等价物解释。

    把抽象的克数换算成常见食物份量，帮助用户建立感知：
      - 热量   → 碗米饭
      - 蛋白质 → 块鸡胸肉 / 个鸡蛋
      - 碳水   → 碗米饭 / 片面包
      - 脂肪   → 汤匙食用油 / 把坚果

    换算基准见模块顶部 ``_CHICKEN_PROTEIN_PER_PIECE`` 等常量；鸡胸肉按
    20g 蛋白 / 100g 锚定（符合常识）。

    Args:
        goal: 已计算好的营养目标（含 min/max 区间）。

    Returns:
        以 ``calories`` / ``protein`` / ``carbs`` / ``fat`` 为键的等价物文案字典。
    """
    calories = _portion_range(goal.calories_min, goal.calories_max, _RICE_BOWL_KCAL)
    chicken = _portion_range(goal.protein_min, goal.protein_max, _CHICKEN_PROTEIN_PER_PIECE)
    eggs = _portion_range(goal.protein_min, goal.protein_max, _EGG_PROTEIN)
    rice = _portion_range(goal.carb_min, goal.carb_max, _RICE_BOWL_CARB)
    bread = _portion_range(goal.carb_min, goal.carb_max, _BREAD_SLICE_CARB)
    oil = _portion_range(goal.fat_min, goal.fat_max, _OIL_TBSP_FAT)
    nuts = _portion_range(goal.fat_min, goal.fat_max, _NUTS_HANDFUL_FAT)
    return {
        "calories": f"相当于 {calories} 碗米饭的热量",
        "protein": f"相当于 {chicken} 块鸡胸肉 或 {eggs} 个鸡蛋",
        "carbs": f"相当于 {rice} 碗米饭 或 {bread} 片面包",
        "fat": f"相当于 {oil} 汤匙食用油 或 {nuts} 把坚果",
    }

# 食材营养库（每 100g 可食部）外置于 app/data/ingredient_nutrition.json，
# 由 load_ingredient_nutrition() 惰性加载并 lru_cache 缓存。Phase 3 扩充至 100+ 种。
# 每条记录含 calories/protein_g/fat_g/carbs_g/default_portion_g/calibration。
_INGREDIENT_NUTRITION = load_ingredient_nutrition()

# 按 key 长度降序预排序，使长名优先于短子串命中（如"鸡蛋"优先于"鸡"）。
# 模块级一次性排序，避免 _ingredient_nutrition_for 每次调用重复排序。
def _ingredient_aliases(keyword: str) -> set[str]:
    """Extract aliases from规范名，如 ``松花蛋（鸭蛋）[皮蛋]``。"""
    aliases = {keyword}
    aliases.update(re.findall(r"\[([^\]]+)\]", keyword))
    aliases.update(re.findall(r"[（(]([^）)]+)[）)]", keyword))
    if keyword == "鸡胸脯肉":
        aliases.add("鸡胸肉")
    return {alias.strip() for alias in aliases if len(alias.strip()) >= 2}


_INGREDIENT_KEYWORDS_BY_LEN = sorted(
    ((alias, keyword) for keyword in _INGREDIENT_NUTRITION for alias in _ingredient_aliases(keyword)),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


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

    匹配优先级: 按 key 长度降序遍历，长名优先于短子串命中——避免"鸡"过早
    截断"鸡蛋"查询。预排序在模块级完成，单次查询不重复排序。
    """
    for alias, keyword in _INGREDIENT_KEYWORDS_BY_LEN:
        if alias in name:
            entry = _INGREDIENT_NUTRITION[keyword]
            portion = entry.get("default_portion_g", 100)
            scale = portion / 100.0
            return {
                "calories": entry["calories"] * scale,
                "protein_g": entry["protein_g"] * scale,
                "fat_g": entry["fat_g"] * scale,
                "carbs_g": entry["carbs_g"] * scale,
            }, True
    return {}, False


def compute_recipe_nutrition(
    ingredients: list[str], servings: int
) -> dict[str, float]:
    """根据食材列表估算菜谱总营养，用于创建菜谱时自动填充 nutrition 字段。

    每个食材按 ``default_portion_g``（单次用量）折算后累加得单份营养，
    再乘以 ``servings`` 得总营养。:func:`estimate_meal_nutrition` 消费时
    会将总营养除以 servings 还原单份，两端语义对齐。

    复用 :func:`_ingredient_nutrition_for`（长名优先 + 别名匹配），保证与
    兜底估算逻辑一致，避免两套算法产生分歧。

    Args:
        ingredients: 食材名列表（如 ``["虾仁", "鸡蛋", "米饭"]``）。
        servings: 菜谱份数。

    Returns:
        总营养字典（``calories`` / ``protein_g`` / ``fat_g`` / ``carbs_g``），
        未命中食材库的食材跳过，保留一位小数。
    """
    per_serving: dict[str, float] = {}
    for name in ingredients:
        nutrition, hit = _ingredient_nutrition_for(name)
        if not hit:
            continue
        for key, value in nutrition.items():
            per_serving[key] = per_serving.get(key, 0.0) + value
    scale = float(max(servings, 1))
    return {key: round(value * scale, 1) for key, value in per_serving.items()}


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
    # 计划生成/历史数据可能没有保存 ingredients，但餐食名称通常仍包含
    # 主要食材（如“皮蛋瘦肉粥”）。用名称做兜底，避免打卡后营养进度恒为 0。
    ingredient_names = list(meal.ingredients or [])
    if not ingredient_names:
        ingredient_names = [meal.name]
    for ingredient in ingredient_names:
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


def nutrition_delta(
    before: dict[str, float], after: dict[str, float]
) -> dict[str, float]:
    """计算营养差异 ``after - before``，键取两者并集，保留一位小数。

    用于餐食替换前后"单餐/全天营养变化"的直观对比。
    """
    keys = set(before) | set(after)
    return {key: round(after.get(key, 0.0) - before.get(key, 0.0), 1) for key in sorted(keys)}


def sum_meal_nutrition(
    meals: Sequence[MealLike],
    recipes: Sequence[RecipeRecord],
    *,
    override_id: int | None = None,
    override_nutrition: dict[str, float] | None = None,
) -> dict[str, float]:
    """汇总一组餐食的营养合计。

    ``override_id`` / ``override_nutrition`` 用于在"替换后"场景下用新餐食的营养
    替换同名餐食的估算值（其余餐食照常估算），从而得到替换后的全天合计。
    """
    total: dict[str, float] = {}
    for meal in meals:
        if override_id is not None and getattr(meal, "id", None) == override_id:
            nutrition: dict[str, float] = override_nutrition or {}
        else:
            nutrition, _ = estimate_meal_nutrition(meal, recipes)
        for key, value in nutrition.items():
            total[key] = total.get(key, 0.0) + value
    return {key: round(value, 1) for key, value in total.items()}
