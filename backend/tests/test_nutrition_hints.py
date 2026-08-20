"""阶段2 营养目标等价物解释（"相当于 X"）的单元测试。

验证 :func:`build_nutrition_hints` 把抽象的克数换算成常见食物份量：
  - 鸡胸肉按 20g 蛋白 / 100g 锚定（一块约 150g ≈ 30g 蛋白）
  - 鸡蛋 ≈ 7g/个、米饭 ≈ 200kcal / 40g 碳水、面包 ≈ 15g 碳水/片
  - 食用油 ≈ 10g/汤匙、坚果 ≈ 12g/把

这些测试只测纯函数，无网络 / 数据库依赖。
"""
from app.models import NutritionGoal
from app.services.nutrition import build_nutrition_hints


def _goal(**overrides: float) -> NutritionGoal:
    """构造最小营养目标，覆盖换算所需的 min/max 区间字段。"""
    defaults: dict[str, float] = {
        "user_id": 1.0,
        "calories_min": 1860.0,
        "calories_max": 2140.0,
        "protein_min": 65.0,
        "protein_max": 85.0,
        "carb_min": 200.0,
        "carb_max": 260.0,
        "fat_min": 45.0,
        "fat_max": 65.0,
    }
    defaults.update(overrides)
    return NutritionGoal(**defaults)  # type: ignore[arg-type]


def test_protein_hint_uses_chicken_breast_20g_anchor() -> None:
    """蛋白质 65~85g → 2~3 块鸡胸肉（20g/100g，一块 150g ≈ 30g）或 9~12 个鸡蛋。"""
    hints = build_nutrition_hints(_goal())
    assert hints["protein"] == "相当于 2~3 块鸡胸肉 或 9~12 个鸡蛋"


def test_calories_hint_maps_to_rice_bowls() -> None:
    """热量 1860~2140 kcal → 9~11 碗米饭（约 200 kcal/碗）。"""
    hints = build_nutrition_hints(_goal())
    assert hints["calories"] == "相当于 9~11 碗米饭的热量"


def test_carbs_hint_maps_to_rice_and_bread() -> None:
    """碳水 200~260g → 5~7 碗米饭 或 13~17 片面包。"""
    hints = build_nutrition_hints(_goal())
    assert hints["carbs"] == "相当于 5~7 碗米饭 或 13~17 片面包"


def test_fat_hint_maps_to_oil_and_nuts() -> None:
    """脂肪 45~65g → 5~7 汤匙食用油 或 4~5 把坚果。"""
    hints = build_nutrition_hints(_goal())
    assert hints["fat"] == "相当于 5~7 汤匙食用油 或 4~5 把坚果"


def test_hint_collapses_to_single_number_when_range_rounds_equal() -> None:
    """上下限四舍五入后相同则折叠为单个数字，避免"5~5 块"赘述。"""
    hints = build_nutrition_hints(_goal(protein_min=60.0, protein_max=60.0))
    # 60/30=2、60/7≈8.57→9
    assert hints["protein"] == "相当于 2 块鸡胸肉 或 9 个鸡蛋"
