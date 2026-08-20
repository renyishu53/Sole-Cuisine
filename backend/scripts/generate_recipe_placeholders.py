"""批量生成菜谱 SVG 占位图（修复 Trae 文生图接口限流导致首页图片加载失败的问题）。

读取 ``app/data/recipes.json``，为每条菜谱生成一张带菜名+分类配色的
SVG 占位图，存到 ``app/static/recipes/{id}.svg``。同步把 ``image_url``
字段更新为本地路径 ``/static/recipes/{id}.svg``，由 FastAPI StaticFiles
挂载直接 serve，零外部依赖、瞬时加载、离线可用。

可重复执行：已存在的 SVG 会被覆盖；recipes.json 中已是本地路径的条目跳过。

用法:
    uv run python -m backend.scripts.generate_recipe_placeholders
"""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

# 项目根目录（脚本相对于 backend/scripts/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DATA_PATH = _BACKEND_DIR / "app" / "data" / "recipes.json"
_STATIC_DIR = _BACKEND_DIR / "app" / "static" / "recipes"

# 按 category 分组的配色：背景渐变 + 标签色 + 主标题色
_CATEGORY_THEME: dict[str, dict[str, str]] = {
    "muscle_gain": {  # 增肌 —— 暖橙，象征能量与力量
        "grad_start": "#d97757",
        "grad_end": "#b35a3c",
        "badge_bg": "#fff3eb",
        "badge_fg": "#a3421b",
        "title_fg": "#ffffff",
        "subtitle_fg": "#ffe5d4",
        "label": "增肌",
    },
    "fat_loss": {  # 减脂 —— 冷青，象征清爽低负担
        "grad_start": "#5b8db8",
        "grad_end": "#3a6d96",
        "badge_bg": "#eaf2f9",
        "badge_fg": "#1f4f78",
        "title_fg": "#ffffff",
        "subtitle_fg": "#d8e8f3",
        "label": "减脂",
    },
    "healthy": {  # 健康 —— 鼠尾草绿，与主品牌色一致
        "grad_start": "#3a7d6b",
        "grad_end": "#2d6253",
        "badge_bg": "#eaf3ec",
        "badge_fg": "#1f574a",
        "title_fg": "#ffffff",
        "subtitle_fg": "#d3ebe3",
        "label": "健康",
    },
}

# 装饰性 SVG 图标（按 category 区分），避免使用 emoji
_DECORATIVE_ICON: dict[str, str] = {
    "muscle_gain": (
        # 哑铃轮廓 —— 增肌主题
        '<g transform="translate(280,140)" fill="rgba(255,255,255,.18)">'
        '<rect x="-6" y="-22" width="12" height="44" rx="3"/>'
        '<rect x="-30" y="-30" width="18" height="14" rx="3"/>'
        '<rect x="-30" y="16" width="18" height="14" rx="3"/>'
        '<rect x="12" y="-30" width="18" height="14" rx="3"/>'
        '<rect x="12" y="16" width="18" height="14" rx="3"/>'
        "</g>"
    ),
    "fat_loss": (
        # 叶子 —— 减脂主题
        '<g transform="translate(280,150)" fill="rgba(255,255,255,.18)">'
        '<path d="M-30,30 Q-10,-10 30,-30 Q10,10 -30,30 Z"/>'
        '<line x1="-30" y1="30" x2="30" y2="-30" stroke="rgba(255,255,255,.3)" stroke-width="2"/>'
        "</g>"
    ),
    "healthy": (
        # 餐盘轮廓 —— 健康主题
        '<g transform="translate(280,150)" fill="rgba(255,255,255,.18)" stroke="rgba(255,255,255,.35)" stroke-width="2">'
        '<circle cx="0" cy="0" r="30"/>'
        '<circle cx="0" cy="0" r="16" fill="none"/>'
        "</g>"
    ),
}


def _build_svg(name: str, theme: dict[str, str]) -> str:
    """为单个菜谱生成 SVG 字符串（400x400 viewBox）。"""
    icon = _DECORATIVE_ICON.get(_reverse_label(theme["label"]), "")
    safe_name = escape(name)
    # 标题文本自动换行：超过 8 字换到下一行
    wrapped_name = _wrap_text(name, max_chars=8)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" width="400" height="400">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{theme['grad_start']}"/>
      <stop offset="100%" stop-color="{theme['grad_end']}"/>
    </linearGradient>
    <radialGradient id="glow" cx="80%" cy="20%" r="50%">
      <stop offset="0%" stop-color="rgba(255,255,255,.25)"/>
      <stop offset="100%" stop-color="rgba(255,255,255,0)"/>
    </radialGradient>
  </defs>
  <rect width="400" height="400" fill="url(#bg)"/>
  <rect width="400" height="400" fill="url(#glow)"/>
  {icon}
  <g transform="translate(40,50)">
    <rect width="68" height="28" rx="14" fill="{theme['badge_bg']}"/>
    <text x="34" y="19" text-anchor="middle" font-family="Microsoft YaHei, PingFang SC, sans-serif" font-size="14" font-weight="700" fill="{theme['badge_fg']}">{theme['label']}</text>
  </g>
  <g font-family="Microsoft YaHei, PingFang SC, sans-serif" text-anchor="middle">
    {''.join(f'<text x="200" y="{210 + i * 42}" font-size="34" font-weight="700" fill="{theme["title_fg"]}">{escape(line)}</text>' for i, line in enumerate(wrapped_name))}
    <text x="200" y="340" font-size="14" fill="{theme['subtitle_fg']}" opacity="0.85">SoloChef 营养菜谱</text>
  </g>
  <line x1="160" y1="360" x2="240" y2="360" stroke="rgba(255,255,255,.4)" stroke-width="2" stroke-linecap="round"/>
</svg>"""  # noqa: E501


def _reverse_label(label: str) -> str:
    """通过 label 反查 category key。"""
    for cat, theme in _CATEGORY_THEME.items():
        if theme["label"] == label:
            return cat
    return "healthy"


def _wrap_text(text: str, max_chars: int) -> list[str]:
    """简单按字符数换行（中英文都按 1 字符计）。"""
    if len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    for i in range(0, len(text), max_chars):
        lines.append(text[i : i + max_chars])
    return lines


def _generate(recipes: list[dict]) -> tuple[int, int]:
    """遍历菜谱列表生成 SVG，返回 (已生成, 跳过) 计数。"""
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    generated = 0
    skipped = 0
    for recipe in recipes:
        recipe_id = recipe.get("id")
        name = recipe.get("name", "")
        category = recipe.get("category", "healthy")
        if not recipe_id or not name:
            continue
        theme = _CATEGORY_THEME.get(category, _CATEGORY_THEME["healthy"])
        svg = _build_svg(name, theme)
        path = _STATIC_DIR / f"{recipe_id}.svg"
        path.write_text(svg, encoding="utf-8")
        generated += 1
    return generated, skipped


def _update_recipes_json(paths: dict[str, str]) -> int:
    """把 recipes.json 中仍指向 trae-api 的 image_url 替换为 /static/...，返回更新条目数。"""
    data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    updated = 0
    for recipe in data:
        rid = recipe.get("id")
        if rid in paths:
            new_url = paths[rid]
            if recipe.get("image_url") != new_url:
                recipe["image_url"] = new_url
                updated += 1
    _DATA_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return updated


def run() -> None:
    parser = argparse.ArgumentParser(description="生成菜谱 SVG 占位图")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将生成的条目数，不实际写文件",
    )
    args = parser.parse_args()

    recipes = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    if args.dry_run:
        print(f"[DRY-RUN] 将为 {len(recipes)} 条菜谱生成 SVG 到 {_STATIC_DIR}")
        return

    generated, skipped = _generate(recipes)
    local_paths = {r["id"]: f"/static/recipes/{r['id']}.svg" for r in recipes if r.get("id")}
    updated = _update_recipes_json(local_paths)
    print(
        f"✅ 生成 {generated} 个 SVG，跳过 {skipped}；"
        f"更新 recipes.json 中 {updated} 个 image_url 为本地路径"
    )


if __name__ == "__main__":
    run()