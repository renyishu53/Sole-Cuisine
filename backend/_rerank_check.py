"""临时验证脚本：验证 reranker 后端真实可用（跑完即删）。"""
import sys

from app.core.config import get_settings
from app.services.reranker import create_rerank_backend

backend = create_rerank_backend(get_settings())
lines = []

if backend is None:
    lines.append("RESULT: FAILED - rerank backend is None")
    open("_rerank_check.txt", "w", encoding="utf-8").write("\n".join(lines))
    sys.exit(1)

lines.append(f"label: {backend.label}")
lines.append(f"model_name: {backend.model_name}")

scores = backend.rerank(
    "孩子不吃辣，周三要快手晚餐",
    ["虾仁滑蛋盖饭：少油快炒 18 分钟出锅，口味清淡", "麻辣香锅：重油重辣，制作 40 分钟", "番茄鸡蛋面：清淡酸甜，20 分钟煮制"],
)
for doc, score in zip(
    ["虾仁滑蛋(清淡快手)", "麻辣香锅(辣)", "番茄鸡蛋面(清淡)"],
    scores,
    strict=False,
):
    lines.append(f"{doc}: {score:.4f}")
lines.append(f"RESULT: OK - scores len={len(scores)}")
open("_rerank_check.txt", "w", encoding="utf-8").write("\n".join(lines))
