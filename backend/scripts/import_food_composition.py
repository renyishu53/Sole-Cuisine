"""《中国食物成分表》OCR 数据集 → SoloChef ingredient_nutrition.json 转换器。

数据集来源: Qwen2.5-VL-72B 从《中国食物成分表（标准版）》图片 OCR 得到的
结构化 JSON，按食物类别分文件存储，文件名形如 ``merged_<类别>-<子类>.json``。
每条记录为每 100g 可食部的营养成分，数值字段均为字符串型，空值用 ``—`` 表示。

本脚本完成:
    1. 遍历数据集所有 ``merged_*.json`` 文件
    2. 字段映射: energyKCal→calories, protein→protein_g, fat→fat_g, CHO→carbs_g
    3. 数值清洗: 字符串转 float, ``—`` 与非法值跳过该条目
    4. 按食物类别补 ``default_portion_g``（独居单餐经验用量）
    5. 合并策略: 保留现有 ingredient_nutrition.json 中已校准条目（优先匹配）,
       数据集仅补充同名之外的新条目

用法:
    # 合并模式（推荐）: 保留现有 105 条已校准条目，数据集补充扩充
    python -m backend.scripts.import_food_composition \
        --src "D:/pyton_feisi/project/project_agent/china-food-data/json_data_vision_251206_Qwen2-5-VL-72B-Instruct" \
        --dst "backend/app/data/ingredient_nutrition.json" \
        --merge

    # 全量替换模式: 仅用数据集生成新文件（会丢失现有 default_portion_g 经验值）
    python -m backend.scripts.import_food_composition \
        --src "<数据集路径>" \
        --dst "backend/app/data/ingredient_nutrition.json"
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 类别 → 独居单餐默认用量（g）。数据集不含此字段，按成分表类别补经验值。
# 依据: 独居成人单餐合理摄入量，肉类 75g / 蔬菜 150g / 主食 150g 等。
# ---------------------------------------------------------------------------
_PORTION_BY_CATEGORY: dict[str, int] = {
    "谷类及其制品": 150,
    "干豆类及其制品": 50,
    "豆类及其制品": 100,
    "蔬菜类及其制品": 150,
    "菌藻类": 100,
    "水果类及其制品": 150,
    "坚果种子类": 25,
    "畜禽肉类及其制品": 75,
    "畜肉类及其制品": 75,
    "禽肉类及其制品": 75,
    "蛋类及其制品": 50,
    "鱼虾蟹贝类": 100,
    "乳类及其制品": 250,
    "调味品、酱咸菜类": 10,
    "调味品类": 10,
    "油脂类": 10,
    "饮料类": 250,
    "酒类": 250,
    "糖果蜜饯类": 25,
    "淀粉类及其制品": 50,
    "婴儿食品": 100,
    "其他": 100,
}

# 文件名 → 类别提取正则: merged_<类别>-<子类>.json
_FILENAME_RE = re.compile(r"^merged_(.+?)-.+\.json$")

# 从字符串中提取首个数值（剥离星号、单位等后缀）。OCR 数据常见脏值:
# "899*"（估算值标注）→ 899, "211.9" → 211.9, "—" → 无匹配
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# 数据集中表示空值的占位符（em dash 或无数字的占位词）
_NULL_TOKENS = frozenset({"—", "-", "", "N/A", "na", "NA", "un", "UN", "微量", "Tr"})


class NutritionParseError(ValueError):
    """数据集条目解析失败（缺关键字段或数值非法）。"""


@dataclass(frozen=True, slots=True)
class RawEntry:
    """数据集原始条目（仅保留项目需要的字段）。"""

    food_name: str
    category: str
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float


def _parse_category(filename: str) -> str:
    """从 ``merged_谷类及其制品-稻米.json`` 提取 ``谷类及其制品``。

    无法识别时返回 ``"其他"``，确保 default_portion_g 永远有兜底。
    """
    match = _FILENAME_RE.match(filename)
    return match.group(1) if match else "其他"


def _to_float(value: object) -> float | None:
    """从字符串提取首个数值并转 float，空值占位符返回 None。

    数据集为 OCR 产物，数值字段均为字符串且带脏值:
        - ``"332"`` 标准数值 → 332.0
        - ``"899*"`` 估算值星号标注 → 899.0（剥离后缀）
        - ``"—"`` / ``"un"`` 空值占位 → None

    采用正则提取而非直接 ``float()``, 可容错处理星号、单位等后缀。
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in _NULL_TOKENS:
        return None
    match = _NUMBER_RE.search(text)
    if match is None:
        logger.warning("无法提取数值: %r", text)
        return None
    return float(match.group())


def _load_existing(dst: Path) -> dict[str, dict[str, object]]:
    """读取现有 ingredient_nutrition.json 的 ingredients 部分，不存在则空 dict。"""
    if not dst.exists():
        return {}
    with dst.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return dict(payload.get("ingredients", {}))


def iter_raw_entries(src_dir: Path) -> Iterator[tuple[RawEntry, str]]:
    """惰性遍历数据集所有 merged_*.json，yield (条目, 来源文件名)。

    缺少关键营养字段的条目会被跳过并记录日志，不中断遍历。
    返回元组附带来源文件名，便于出错时定位。
    """
    for json_file in sorted(src_dir.glob("merged_*.json")):
        category = _parse_category(json_file.name)
        with json_file.open(encoding="utf-8") as handle:
            try:
                records = json.load(handle)
            except json.JSONDecodeError as exc:
                logger.error("JSON 解析失败 %s: %s", json_file.name, exc)
                continue
        if not isinstance(records, list):
            logger.warning("非数组结构，跳过 %s", json_file.name)
            continue
        for record in records:
            entry = _parse_record(record, category, json_file.name)
            if entry is not None:
                yield entry, json_file.name


def _parse_record(
    record: dict[str, object], category: str, source: str
) -> RawEntry | None:
    """单条记录 → RawEntry，关键字段缺失返回 None。"""
    food_name = str(record.get("foodName", "")).strip()
    if not food_name:
        return None
    calories = _to_float(record.get("energyKCal"))
    protein = _to_float(record.get("protein"))
    fat = _to_float(record.get("fat"))
    carbs = _to_float(record.get("CHO"))
    # 关键四字段任一缺失则跳过（无法支撑 Verifier 计算）
    if calories is None or protein is None or fat is None or carbs is None:
        logger.debug("跳过 %s（关键字段缺失）来源=%s", food_name, source)
        return None
    return RawEntry(
        food_name=food_name,
        category=category,
        calories=calories,
        protein_g=protein,
        fat_g=fat,
        carbs_g=carbs,
    )


def to_ingredient(entry: RawEntry) -> dict[str, object]:
    """RawEntry → 项目 schema（含 default_portion_g 与 calibration）。"""
    portion = _PORTION_BY_CATEGORY.get(entry.category, 100)
    return {
        "calories": entry.calories,
        "protein_g": entry.protein_g,
        "fat_g": entry.fat_g,
        "carbs_g": entry.carbs_g,
        "default_portion_g": portion,
        "calibration": "verified",
    }


# 匹配首个全角/半角括号或方括号及其后所有内容:
# 鸡蛋（代表值）→ 鸡蛋, 番茄 [西红柿] → 番茄, 猪肉（后臀尖）→ 猪肉
_BRACKET_RE = re.compile(r"[（(\[].*")


def _normalize_name(food_name: str) -> tuple[str, bool]:
    """剥离括号/方括号备注，返回 (核心名, 是否为'代表值'条目)。

    数据集命名形如 ``鸡蛋（代表值）`` / ``番茄 [西红柿]`` / ``猪肉（后臀尖）``,
    项目子串匹配 (``if keyword in name``) 需泛类名作为 key 才能命中查询。
    剥离后 ``鸡蛋（代表值）`` 与 ``鸡蛋（白皮）`` 都产生核心名 ``鸡蛋``,
    碰撞时由 :func:`build_database` 按"代表值优先"裁决。

    Args:
        food_name: 数据集原始 foodName。

    Returns:
        (核心名, 是否含'代表值'标记)。原名无括号时核心名与原名相同。
    """
    core = _BRACKET_RE.sub("", food_name).strip()
    is_representative = "代表值" in food_name
    return core, is_representative


# 常见查询名 → 数据集核心名别名映射。
# 数据集命名与项目常见查询名存在差异（如数据集用"纯牛奶"，项目查"牛奶"），
# 此表为这些差异生成别名 key，指向数据集核心名条目，使子串匹配能命中。
# 仅当目标核心名存在于数据集时别名才生效（见 build_database 末尾）。
_NAME_ALIASES: dict[str, str] = {
    "鸡肉": "鸡",        # 数据集无"鸡肉"泛类，用禽类代表值"鸡"
    "牛奶": "纯牛奶",    # 数据集用"纯牛奶（代表值，全脂）"
    "青菜": "小白菜",    # 数据集用"小白菜[青菜]"，方括号别名被剥离
}


def build_database(
    src_dir: Path, existing: dict[str, dict[str, object]], merge: bool
) -> tuple[dict[str, object], dict[str, int]]:
    """聚合生成最终 JSON 结构，返回 (database, 统计信息)。

    名字规范化（全量替换模式核心）: 数据集 foodName 形如 ``鸡蛋（代表值）``,
    剥离括号后生成泛类核心名 ``鸡蛋`` 作为额外 key, 使 nutrition.py 的子串匹配
    (``if keyword in name``) 能命中泛类查询。核心名碰撞时"代表值"条目优先,
    保证泛类 key 取该食材的典型营养值。原名 key 全部保留供细分查询。

    合并模式: existing 条目放最前（保持经验值优先匹配）, 数据集仅补充新条目,
    且数据集核心名不覆盖 existing 已有的同名泛类。
    """
    ingredients: dict[str, dict[str, object]] = {}
    if merge:
        ingredients.update(existing)
    stats = {
        "existing_kept": len(ingredients),
        "dataset_total": 0,
        "dataset_skipped": 0,
        "dataset_added": 0,
        "duplicates": 0,
        "core_names_generated": 0,
        "core_overrides_by_representative": 0,
        "aliases_generated": 0,
    }
    # 核心名碰撞追踪: core -> (ingredient, is_representative)
    # 碰撞时"代表值"覆盖非代表值, 确保泛类 key 取该食材的典型营养值
    core_seen: dict[str, tuple[dict[str, object], bool]] = {}
    for entry, _source in iter_raw_entries(src_dir):
        stats["dataset_total"] += 1
        original = entry.food_name
        ingredient = to_ingredient(entry)
        core, is_repr = _normalize_name(original)

        # 原名 key: 数据集内重名只保留首个（保留细分条目供精确查询）
        if original in ingredients:
            stats["duplicates"] += 1
        else:
            ingredients[original] = ingredient
            stats["dataset_added"] += 1

        # 核心名 key: 仅当剥离出与原名不同的核心名时, 生成额外泛类条目
        # 碰撞裁决: "代表值"覆盖非代表值; merge 模式下不覆盖 existing 已有泛类
        if core != original:
            existing_core = core_seen.get(core)
            if existing_core is None:
                core_seen[core] = (ingredient, is_repr)
                if core not in ingredients:
                    ingredients[core] = ingredient
                    stats["core_names_generated"] += 1
            elif is_repr and not existing_core[1]:
                core_seen[core] = (ingredient, is_repr)
                ingredients[core] = ingredient
                stats["core_overrides_by_representative"] += 1
    stats["dataset_skipped"] = (
        stats["dataset_total"] - stats["dataset_added"] - stats["duplicates"]
    )

    # 应用别名映射: 为常见查询名生成额外 key, 指向数据集核心名条目。
    # 仅当别名未被占用且目标核心名存在时生效，避免覆盖已有条目。
    for alias, target_core in _NAME_ALIASES.items():
        if alias not in ingredients and target_core in ingredients:
            ingredients[alias] = ingredients[target_core]
            stats["aliases_generated"] += 1

    database = {
        "_meta": {
            "name": "SoloChef 食材营养库",
            "version": "2.0.0",
            "unit": "每 100g 可食部",
            "source": (
                "《中国食物成分表（标准版）第6版》Qwen2.5-VL-72B OCR 结构化数据集"
                "（约1677条），合并原有 105 条手工校准条目"
                if merge
                else "《中国食物成分表（标准版）第6版》Qwen2.5-VL-72B OCR 结构化数据集"
            ),
            "updated_at": "2026-08-11",
            "name_normalization": (
                "none"
                if merge
                else (
                    "剥离括号/方括号备注生成泛类核心名(如'鸡蛋（代表值）'→'鸡蛋'),"
                    "核心名碰撞时优先保留'代表值'条目; 原名与核心名双 key 共存"
                )
            ),
            "schema": {
                "calories": "kcal",
                "protein_g": "蛋白质(g)",
                "fat_g": "脂肪(g)",
                "carbs_g": "碳水化合物(g)",
                "default_portion_g": "单次烹饪默认用量(g)，用于无菜谱匹配时的保守估算",
            },
            "calibration_legend": {
                "verified": "已对照权威成分表校准，数值可靠",
                "estimated": "依据近似食材或经验系数估算，需后续校准",
            },
            "merge_strategy": "existing_first_override" if merge else "full_replace",
        },
        "ingredients": ingredients,
    }
    return database, stats


def main() -> None:
    """命令行入口: 解析参数、转换、写文件、打印统计。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="《中国食物成分表》OCR 数据集 → SoloChef ingredient_nutrition.json",
    )
    parser.add_argument("--src", type=Path, required=True, help="数据集文件夹路径")
    parser.add_argument("--dst", type=Path, required=True, help="输出 JSON 路径")
    parser.add_argument(
        "--merge",
        action="store_true",
        help="合并模式: 保留现有 ingredient_nutrition.json 已校准条目（推荐）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计不写文件，用于预检数据集",
    )
    args = parser.parse_args()

    if not args.src.is_dir():
        raise SystemExit(f"数据集路径不存在或非目录: {args.src}")

    existing = _load_existing(args.dst) if args.merge else {}
    if args.merge and existing:
        logger.info("合并模式: 保留现有 %d 条已校准条目", len(existing))

    database, stats = build_database(args.src, existing, args.merge)

    print("\n========== 转换统计 ==========")
    print(f"  现有条目保留:   {stats['existing_kept']}")
    print(f"  数据集总条目:   {stats['dataset_total']}")
    print(f"  数据集新增:     {stats['dataset_added']}")
    print(f"  与现有重复:     {stats['duplicates']}")
    print(f"  数据集跳过:     {stats['dataset_skipped']}（关键字段缺失或非法）")
    print(f"  核心名生成:     {stats['core_names_generated']}（剥离括号的泛类 key）")
    print(f"  代表值覆盖:     {stats['core_overrides_by_representative']}（碰撞时取代表值）")
    print(f"  别名生成:       {stats['aliases_generated']}（常见查询名→核心名）")
    print(f"  最终总条目:     {len(database['ingredients'])}")
    print("==============================\n")

    if args.dry_run:
        logger.info("dry-run 模式，未写入文件")
        return

    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(
        json.dumps(database, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("已写入 %s", args.dst)


if __name__ == "__main__":
    main()
