"""购物项单位换算与归一化服务。

本服务将中文购物场景中常见的重量、容量、数量单位换算到统一的基础单位（克 / 毫升 / 个），
供购物合并、预算估算和库存计算复用。设计遵循 python-patterns 的 EAFP 与显式配置原则：

- 所有换算系数集中维护在 ``_CONVERSION_TABLE``，便于审计与扩展。
- ``parse_quantity`` 容错解析 "500 克"、"1.5 斤"、"2 个" 等中文数量字符串。
- ``normalize`` 返回 (基础单位, 基础数量) 元组，无法识别时返回原值。
- ``add_quantities`` 在同一基础单位下相加，跨单位返回 None。
"""

from __future__ import annotations

import re
from typing import NamedTuple


class NormalizedQuantity(NamedTuple):
    """归一化后的数量，统一以基础单位表达。"""

    base_unit: str  # 例如 "g"、"ml"、"个"
    base_value: float
    original_unit: str
    original_value: float
    converted: bool  # 是否发生了单位换算


# 重量单位 -> 克（g）
_WEIGHT_TO_GRAM: dict[str, float] = {
    "克": 1.0,
    "g": 1.0,
    "公斤": 1000.0,
    "kg": 1000.0,
    "千克": 1000.0,
    "斤": 500.0,
    "两": 50.0,
    "磅": 453.592,
    "lb": 453.592,
}

# 容量单位 -> 毫升（ml）
_VOLUME_TO_MILLILITER: dict[str, float] = {
    "毫升": 1.0,
    "ml": 1.0,
    "升": 1000.0,
    "L": 1000.0,
    "l": 1000.0,
}

# 计数单位 -> 个
_COUNT_UNITS: set[str] = {"个", "只", "瓶", "袋", "盒", "包", "份", "打"}

# 别名归一化（用户输入 → 标准单位）
_ALIASES: dict[str, str] = {
    "公克": "克",
    "市斤": "斤",
    "千克": "公斤",
    "公升": "升",
}

_QUANTITY_PATTERN = re.compile(
    r"\s*(\d+(?:\.\d+)?)\s*([^\d\s]+)\s*"
)


def _resolve_alias(unit: str) -> str:
    """将别名解析为标准单位。"""
    return _ALIASES.get(unit, unit)


def parse_quantity(quantity: str) -> tuple[float, str] | None:
    """解析 "数量 单位" 格式字符串。

    Args:
        quantity: 例如 "500 克"、"1.5 斤"、"2 个"。

    Returns:
        (数值, 单位) 元组，无法解析时返回 None。
    """
    match = _QUANTITY_PATTERN.fullmatch(quantity)
    if match is None:
        return None
    value = float(match.group(1))
    unit = _resolve_alias(match.group(2))
    return value, unit


def normalize(quantity: str) -> NormalizedQuantity | None:
    """将购物数量字符串归一化到基础单位。

    Args:
        quantity: 例如 "2 斤"、"500ml"、"3 个"。

    Returns:
        NormalizedQuantity 描述归一化结果；无法识别单位时返回 None。
    """
    parsed = parse_quantity(quantity)
    if parsed is None:
        return None
    value, unit = parsed

    if unit in _WEIGHT_TO_GRAM:
        return NormalizedQuantity(
            base_unit="g",
            base_value=round(value * _WEIGHT_TO_GRAM[unit], 3),
            original_unit=unit,
            original_value=value,
            converted=unit != "克" and unit != "g",
        )
    if unit in _VOLUME_TO_MILLILITER:
        return NormalizedQuantity(
            base_unit="ml",
            base_value=round(value * _VOLUME_TO_MILLILITER[unit], 3),
            original_unit=unit,
            original_value=value,
            converted=unit != "毫升" and unit != "ml",
        )
    if unit in _COUNT_UNITS:
        return NormalizedQuantity(
            base_unit="个",
            base_value=value,
            original_unit=unit,
            original_value=value,
            converted=unit != "个",
        )
    return None


def add_quantities(quantities: list[str]) -> str | None:
    """尝试将多个数量字符串相加。

    当所有数量都能归一化到同一基础单位时返回求和后的可读字符串
    （例如 "1000 克"）；否则返回 None，调用方应保留原值。
    """
    normalized = [normalize(q) for q in quantities]
    if any(item is None for item in normalized):
        return None
    base_units = {item.base_unit for item in normalized if item is not None}
    if len(base_units) != 1:
        return None
    base_unit = base_units.pop()
    total = sum(item.base_value for item in normalized if item is not None)
    # 选取展示单位：克/毫升优先用基础单位；个保留原计数
    if base_unit == "个":
        return f"{total:g} 个"
    if base_unit == "g":
        return f"{total:g} 克"
    if base_unit == "ml":
        return f"{total:g} 毫升"
    return f"{total:g} {base_unit}"


def describe_conversion(quantity: str) -> str | None:
    """生成人类可读的换算说明，用于前端展示。

    示例：
        "2 斤" -> "2 斤 ≈ 1000 克"
        "500ml" -> "500ml = 500 毫升"（同基础单位，不展示）
        "3 个" -> None
    """
    result = normalize(quantity)
    if result is None or not result.converted:
        return None
    return (
        f"{result.original_value:g} {result.original_unit} "
        f"≈ {result.base_value:g} {result.base_unit}"
    )
