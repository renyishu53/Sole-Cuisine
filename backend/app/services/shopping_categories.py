"""Canonical shopping categories shared by generation, APIs, and UI."""

from __future__ import annotations

SHOPPING_CATEGORIES: tuple[str, ...] = ("肉蛋奶", "蔬菜", "主食", "水果", "其他")

_ALIASES = {
    "肉类": "肉蛋奶",
    "蛋类": "肉蛋奶",
    "乳制品": "肉蛋奶",
    "奶制品": "肉蛋奶",
    "调味料": "其他",
    "调味品": "其他",
    "日用品": "其他",
    "未分类": "其他",
    "杂项": "其他",
}

_FRUIT_KEYWORDS = (
    "苹果", "香蕉", "橙", "柑", "橘", "柚", "葡萄", "草莓", "蓝莓",
    "桃", "梨", "猕猴桃", "芒果", "菠萝", "西瓜", "哈密瓜", "樱桃",
)


def normalize_shopping_category(category: str | None, name: str = "") -> str:
    """Normalize any model or legacy value into the five user-facing groups."""
    value = (category or "").strip()
    if value in SHOPPING_CATEGORIES:
        if value == "其他" and any(keyword in name for keyword in _FRUIT_KEYWORDS):
            return "水果"
        return value
    if value in _ALIASES:
        mapped = _ALIASES[value]
        if mapped == "其他" and any(keyword in name for keyword in _FRUIT_KEYWORDS):
            return "水果"
        return mapped
    if any(keyword in name for keyword in _FRUIT_KEYWORDS):
        return "水果"
    return "其他"
