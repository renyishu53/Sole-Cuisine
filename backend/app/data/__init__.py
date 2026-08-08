"""外置营养与菜谱数据包。

将营养数据从代码内置字典迁移到独立 JSON 文件，便于非开发人员维护与扩充。
模块级常量在首次导入时惰性加载，全进程共享一份只读副本。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

_DATA_DIR = Path(__file__).resolve().parent


class IngredientNutrition(TypedDict):
    """单种食材的营养贡献（每 100g 可食部）与校准元信息。"""

    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float
    default_portion_g: float
    calibration: str  # "verified" | "estimated"


class IngredientDatabase(TypedDict):
    """食材营养库的顶层结构，含元信息与食材条目。"""

    _meta: dict[str, object]
    ingredients: dict[str, IngredientNutrition]


@lru_cache(maxsize=1)
def load_ingredient_nutrition() -> dict[str, IngredientNutrition]:
    """加载食材营养库，返回食材名 → 营养字典的映射。

    使用 ``lru_cache`` 保证全进程只读取一次磁盘文件，后续调用走缓存。
    """
    path = _DATA_DIR / "ingredient_nutrition.json"
    with path.open(encoding="utf-8") as handle:
        database: IngredientDatabase = json.load(handle)
    return dict(database["ingredients"])
