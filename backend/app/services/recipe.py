import json
from pathlib import Path
from functools import lru_cache

from app.schemas.domain import (
    RecipeDetail,
    RecipeListResponse,
    RecipeSummary,
)

_DATA_PATH = Path(__file__).parent.parent / "data" / "recipes.json"


@lru_cache(maxsize=1)
def _load_recipes() -> list[dict]:
    with _DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


class RecipeService:
    def list_recipes(
        self,
        category: str | None = None,
        page: int = 1,
        page_size: int = 8,
    ) -> RecipeListResponse:
        recipes = _load_recipes()
        if category and category != "all":
            recipes = [r for r in recipes if r["category"] == category]
        total = len(recipes)
        start = (page - 1) * page_size
        end = start + page_size
        page_recipes = recipes[start:end]
        return RecipeListResponse(
            recipes=[RecipeSummary(**r) for r in page_recipes],
            total=total,
            page=page,
            page_size=page_size,
            has_more=end < total,
        )

    def get_recipe(self, recipe_id: str) -> RecipeDetail | None:
        recipes = _load_recipes()
        for r in recipes:
            if r["id"] == recipe_id:
                return RecipeDetail(**r)
        return None

    def similar_recipes(self, recipe_id: str, limit: int = 3) -> list[RecipeSummary]:
        """按标签重叠 + 同分类返回相似菜谱（静态菜谱池的确定性检索）。"""
        recipes = _load_recipes()
        source = next((r for r in recipes if r["id"] == recipe_id), None)
        if source is None:
            return []
        source_tags = set(source.get("tags", []))
        scored: list[tuple[int, dict]] = []
        for r in recipes:
            if r["id"] == recipe_id:
                continue
            shared = len(source_tags & set(r.get("tags", [])))
            same_category = r["category"] == source["category"]
            score = shared * 2 + (1 if same_category else 0)
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [RecipeSummary(**r) for _, r in scored[:limit]]

    def recipe_tip(self, recipe_id: str) -> str | None:
        """返回该菜谱的营养师小贴士（确定性生成，按 id 进程内缓存）。"""
        if next((r for r in _load_recipes() if r["id"] == recipe_id), None) is None:
            return None
        return build_recipe_tip(recipe_id)


@lru_cache(maxsize=64)
def build_recipe_tip(recipe_id: str) -> str:
    """由营养数值 + 标签 + 耗时推导出营养师风格的小贴士（无 LLM 依赖）。"""
    recipe = next(r for r in _load_recipes() if r["id"] == recipe_id)
    name = recipe["name"]
    nutrition = recipe.get("nutrition", {})
    calories = float(nutrition.get("calories", 0) or 0)
    protein = float(nutrition.get("protein", 0) or 0)
    fat = float(nutrition.get("fat", 0) or 0)
    prep = int(recipe.get("prep_time", 0) or 0)
    tags = set(recipe.get("tags", []))
    tips: list[str] = []
    if calories > 0 and protein > 0:
        ratio = protein * 4 / calories
        if ratio >= 0.3:
            tips.append(f"蛋白质供能占比约 {ratio:.0%}，适合增肌期补充")
        elif ratio <= 0.15:
            tips.append("蛋白质偏低，建议搭配鸡蛋或豆制品补齐")
    if fat >= 20:
        tips.append("脂肪偏高，可用蒸煮替代煎炒、减少烹调油")
    if calories <= 200:
        tips.append("热量较低，作为正餐可再配一份主食或蛋白质")
    if prep <= 10:
        tips.append("十分钟内可完成，很适合忙碌工作日的快手晚餐")
    if "高蛋白" in tags:
        tips.append("已标注高蛋白，运动后食用有助肌肉恢复")
    if not tips:
        tips.append("整体营养较均衡，注意搭配一份深色蔬菜")
    return f"「{name}」营养师小贴士：{'；'.join(tips)}。"


recipe_service = RecipeService()
