"""Reranker backend factory with bge-reranker-v2-m3 support and graceful fallback.

The reranker performs a second-stage refinement over the first-stage vector
retrieval results. `bge-reranker-v2-m3` is an optional dependency (the
`FlagEmbedding` package): when it is missing, the local model files are absent,
or the model fails to load, retrieval proceeds with the first-stage ranking and
the chain is never interrupted.

The design deliberately mirrors :mod:`app.services.embeddings` — a frozen
dataclass carrying the resolved callable plus display metadata, and a factory
that never triggers an implicit multi-GB download.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from app.core.config import Settings


class SupportsRerank(Protocol):
    def compute_score(
        self, pairs: list[list[str]], **kwargs: Any
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class RerankBackend:
    """Resolved reranker model plus display metadata."""

    model: Any
    model_name: str
    label: str

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score each (query, document) pair; higher is more relevant."""
        if not documents:
            return []
        pairs = [[query, doc] for doc in documents]
        scores = self.model.compute_score(pairs, normalize=True)  # type: ignore[attr-defined]
        if hasattr(scores, "tolist"):
            scores = scores.tolist()  # numpy / torch tensor
        return [float(value) for value in scores]


def create_rerank_backend(settings: Settings) -> RerankBackend | None:
    """Resolve the reranker backend, returning ``None`` when unavailable."""
    if not settings.rerank_enabled:
        return None
    try:
        from FlagEmbedding import FlagModel
    except ImportError:
        logger.warning("FlagEmbedding 未安装，二阶段精排（rerank）不可用，退回首阶段排序")
        return None
    local_path = settings.rerank_model_path.strip()
    if local_path and not Path(local_path).is_dir():
        logger.warning("rerank 本地模型目录不存在: {}", local_path)
        return None
    model_ref = local_path or settings.rerank_model
    try:
        model = FlagModel(
            model_ref,
            use_fp16=False,
            device=settings.rerank_device,
            # 与 BGE-M3 一致：绝不触发隐式下载，模型必须预置于本地。
            local_files_only=True,
        )
    except Exception as exc:
        logger.warning("rerank 模型加载失败 ({}): {}", model_ref, type(exc).__name__)
        return None
    return RerankBackend(
        model=model,
        model_name=f"bge-reranker-v2-m3 ({model_ref})",
        label="二阶段精排模型 bge-reranker-v2-m3",
    )
