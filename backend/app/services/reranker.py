"""Reranker backend factory with bge-reranker-v2-m3 support and graceful fallback.

The reranker performs a second-stage refinement over the first-stage vector
retrieval results. `bge-reranker-v2-m3` is an optional dependency (the
`FlagEmbedding` package): when it is missing, the local model files are absent,
or the model fails to load, retrieval proceeds with the first-stage ranking and
the chain is never interrupted.

The design deliberately mirrors :mod:`app.services.embeddings` — a frozen
dataclass carrying the resolved callable plus display metadata, and a factory
that never triggers an implicit multi-GB download.

FlagEmbedding 1.3+ 兼容要点：
- ``FlagModel`` 在 1.3+ 已变为 embedder（无 ``compute_score``），reranker 须用
  ``FlagReranker``；1.2.x 经典版两者均有，优先取 ``FlagReranker``。
- ``**kwargs`` 中的 ``local_files_only`` 不会被转发给 ``from_pretrained``，
  离线保证只能靠「解析为本地快照目录后按路径加载」实现：
  ``snapshot_download(repo, local_files_only=True)`` 命中缓存返回本地路径，
  未缓存则抛错——绝不触发隐式下载。

transformers 5.x 兼容要点：
- transformers 5 移除了 ``PreTrainedTokenizerBase.prepare_for_model``，而
  FlagEmbedding <=1.4.0 的 ``compute_score`` 依赖它，运行时抛
  ``AttributeError``。此时降级为直接使用 FlagReranker 已加载的
  ``model``/``tokenizer`` 走稳定标准 API（``tokenizer(text, text_pair)`` +
  前向 + sigmoid）自行打分，语义与 ``compute_score(normalize=True)`` 一致。
"""

import inspect
import math
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
        try:
            scores = self.model.compute_score(pairs, normalize=True)  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            # transformers 5.x 移除 prepare_for_model，FlagEmbedding <=1.4.0 的
            # compute_score 不可用；改用已加载 model/tokenizer 直接打分
            scores = _direct_score(self.model, query, documents)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()  # numpy / torch tensor
        return [float(value) for value in scores]


def create_rerank_backend(settings: Settings) -> RerankBackend | None:
    """Resolve the reranker backend, returning ``None`` when unavailable.

    依赖 ``FlagEmbedding`` 包与本地 bge-reranker-v2-m3 模型（显式路径或 HF
    缓存快照）；二者任一缺失即优雅降级为 ``None``（检索链路继续用首阶段
    排序，不中断）。绝不触发隐式下载。
    """
    if not settings.rerank_enabled:
        return None
    model_cls = _resolve_reranker_class()
    if model_cls is None:
        logger.warning("FlagEmbedding 未安装，二阶段精排（rerank）不可用，退回首阶段排序")
        return None
    model_ref = _resolve_model_ref(settings)
    if model_ref is None:
        return None
    try:
        model = _load_reranker(model_cls, model_ref, settings)
    except Exception as exc:
        logger.warning("rerank 模型加载失败 ({}): {}", model_ref, type(exc).__name__)
        return None
    return RerankBackend(
        model=model,
        model_name=f"bge-reranker-v2-m3 ({model_ref})",
        label="二阶段精排模型 bge-reranker-v2-m3",
    )


def _resolve_reranker_class() -> Any | None:
    """优先 ``FlagReranker``（1.2+ 均导出），避免 1.3+ 误用 embedder FlagModel。"""
    try:
        from FlagEmbedding import FlagReranker
    except ImportError:
        try:
            from FlagEmbedding import FlagModel as FlagReranker  # type: ignore[no-redef]
        except ImportError:
            return None
    return FlagReranker


def _resolve_model_ref(settings: Settings) -> str | None:
    """解析为本地模型引用：显式路径优先，其次 HF 缓存快照（绝不下载）。"""
    local_path = settings.rerank_model_path.strip()
    if local_path:
        if Path(local_path).is_dir():
            return local_path
        logger.warning("rerank 本地模型目录不存在: {}", local_path)
        return None
    try:
        from huggingface_hub import snapshot_download

        # local_files_only=True：仅命中本地缓存返回路径，未缓存直接抛错
        return str(snapshot_download(settings.rerank_model, local_files_only=True))
    except Exception:
        logger.info(
            "HF 缓存中未找到 {} ，rerank 不可用（如需启用请在 RERANK_MODEL_PATH "
            "配置本地模型目录）",
            settings.rerank_model,
        )
        return None


def _load_reranker(model_cls: Any, model_ref: str, settings: Settings) -> Any:
    """加载 reranker，兼容 devices (1.2+/1.4) 与 device（旧版）参数名。"""
    parameters = inspect.signature(model_cls.__init__).parameters
    kwargs: dict[str, Any] = {"use_fp16": False}
    if "devices" in parameters:
        kwargs["devices"] = [settings.rerank_device]
    elif "device" in parameters:
        kwargs["device"] = settings.rerank_device
    return model_cls(model_ref, **kwargs)


def _sigmoid(value: float) -> float:
    """数值稳定的 sigmoid（与 FlagEmbedding ``normalize=True`` 语义一致）。"""
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _direct_score(reranker: Any, query: str, documents: list[str]) -> list[float]:
    """绕过 FlagEmbedding ``compute_score``，直接用其已加载组件打分。

    仅使用 transformers 稳定公共 API（``tokenizer(text, text_pair)`` 调用 +
    前向推理），在 4.x 与 5.x 上行为一致。
    """
    import torch

    tokenizer = reranker.tokenizer
    model = reranker.model
    device = next(model.parameters()).device
    max_length = int(getattr(reranker, "max_length", 0) or 512)
    encoded = tokenizer(
        [query] * len(documents),
        documents,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    encoded = {key: value.to(device) for key, value in encoded.items()}
    with torch.no_grad():
        logits = model(**encoded).logits
    values = logits.view(-1).float().cpu().tolist()
    return [_sigmoid(float(value)) for value in values]
