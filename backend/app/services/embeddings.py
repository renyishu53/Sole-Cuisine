"""Embedding backend factory with BGE-M3 support and graceful fallback.

The Milvus collection is bound to a fixed vector dimension, so the backend
is resolved once per process and cached by the caller. BGE-M3 is an optional
dependency: when ``sentence-transformers`` or the local model files are
missing, the store falls back to a lightweight MiniLM model.

稀疏检索（可选增强）：当 ``sparse_enabled`` 且 FlagEmbedding 可用时，
优先走 ``BGEM3FlagModel`` 双输出路径——同一份本地模型文件同时产出稠密
向量（1024 维）与 lexical 稀疏权重（近似 BM25 词法信号），稠密/稀疏
token 空间天然对齐。任一依赖缺失则回退 sentence-transformers 纯稠密路径。
"""

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
from loguru import logger

from app.core.config import Settings


class SupportsEncode(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...


# Milvus SPARSE_FLOAT_VECTOR 格式：{token_id: weight}
SparseVector = dict[int, float]


@dataclass(frozen=True, slots=True)
class EmbeddingBackend:
    """Resolved embedding function plus display metadata."""

    function: Any
    model_name: str
    label: str
    is_bge_m3: bool
    # 可选稀疏编码器（BGEM3SparseEmbedding）；None 表示该后端仅支持稠密
    sparse_encoder: Any = None

    @property
    def supports_sparse(self) -> bool:
        """是否具备 lexical 稀疏输出能力。"""
        return self.sparse_encoder is not None

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to a list of float vectors (Milvus-compatible format)."""
        result = self.function(list(texts))
        # numpy ndarray → list[list[float]]
        if hasattr(result, "tolist"):
            return result.tolist()
        # Already iterable of vectors
        return [list(v) for v in result]

    def encode_both(
        self, texts: list[str]
    ) -> tuple[list[list[float]], list[SparseVector] | None]:
        """一次前向同时产出稠密向量与稀疏权重。

        无稀疏能力时返回 ``(dense, None)``，调用方据此走纯稠密路径。
        """
        if self.sparse_encoder is None:
            return self.encode(texts), None
        dense, sparse = self.sparse_encoder.encode_both(list(texts))
        if hasattr(dense, "tolist"):
            dense = dense.tolist()
        return dense, sparse


class SentenceTransformerEmbedding:
    """SentenceTransformer 适配器，返回归一化向量。

    ``__call__`` 返回 numpy 数组（归一化后的嵌入向量），
    供 EmbeddingBackend.encode() 统一转换为 list 格式后插入 Milvus。
    """

    def __init__(self, model: SupportsEncode) -> None:
        self._model = model

    def __call__(self, input: list[str]) -> Any:
        return self._model.encode(list(input), normalize_embeddings=True)

    @staticmethod
    def name() -> str:
        return "bge-m3-sentence-transformers"


class BGEM3SparseEmbedding:
    """FlagEmbedding BGEM3FlagModel 适配器：稠密 + lexical 稀疏双输出。

    与 SentenceTransformerEmbedding 加载同一份 BGE-M3 模型文件，但通过
    BGEM3FlagModel 暴露 sparse 头（sentence-transformers 会屏蔽该输出）。
    ``__call__`` 保持稠密协议兼容，``encode_both`` 一次前向拿双输出。
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    def __call__(self, input: list[str]) -> Any:
        output = self._model.encode(list(input), return_dense=True, return_sparse=False)
        return _normalize_dense(output["dense_vecs"])

    def encode_sparse(self, input: list[str]) -> list[SparseVector]:
        output = self._model.encode(list(input), return_dense=True, return_sparse=True)
        return [_normalize_sparse(weights) for weights in output["lexical_weights"]]

    def encode_both(self, input: list[str]) -> tuple[Any, list[SparseVector]]:
        output = self._model.encode(list(input), return_dense=True, return_sparse=True)
        dense = _normalize_dense(output["dense_vecs"])
        sparse = [_normalize_sparse(weights) for weights in output["lexical_weights"]]
        return dense, sparse


def _normalize_dense(dense: Any) -> Any:
    """显式 L2 归一化，不依赖模型内部默认行为（COSINE 指标要求模长 1）。"""
    if not hasattr(dense, "ndim"):
        return dense
    norms = np.linalg.norm(dense, axis=1, keepdims=True)
    return dense / np.clip(norms, 1e-12, None)


def _normalize_sparse(weights: Any) -> SparseVector:
    """lexical_weights → Milvus 稀疏格式 ``{token_id: weight}``。

    FlagEmbedding 各版本返回 dict[str, float]（key 为 str(token_id)），
    值可能为 torch 标量；统一转 int/float 并过滤零权重。
    """
    items = weights.items() if isinstance(weights, dict) else weights
    vector: SparseVector = {}
    for key, value in items:
        try:
            token_id, weight = int(key), float(value)
        except (TypeError, ValueError):
            continue
        if weight != 0.0:
            vector[token_id] = weight
    return vector


def create_embedding_backend(settings: Settings) -> EmbeddingBackend:
    """Resolve the embedding backend, falling back to the lightweight model."""
    provider = settings.embedding_provider.strip().lower()
    if provider in {"auto", "bge-m3", "bge_m3"}:
        if settings.sparse_enabled:
            backend = _try_bge_m3_sparse(settings)
            if backend is not None:
                return backend
        backend = _try_bge_m3(settings)
        if backend is not None:
            return backend
        if provider != "auto":
            logger.warning("BGE-M3 向量模型不可用，回退到内置轻量模型")
    return _default_backend()


def _default_backend() -> EmbeddingBackend:
    """轻量兜底模型：all-MiniLM-L6-v2（384 维）。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise RuntimeError(
            "sentence-transformers 未安装，无法初始化向量模型。"
            "请安装 ai 可选依赖：uv sync --extra ai"
        )
    model = SentenceTransformer(
        "sentence-transformers/all-MiniLM-L6-v2",
        device="cpu",
        # 与 _try_bge_m3 一致：绝不隐式触发多 GB 下载，模型须预先缓存到本地。
        # 离线环境下 local_files_only=False 会让 HTTP 请求挂起（而非抛错），
        # 进而卡死整个 RAG 检索链路。
        local_files_only=True,
    )
    return EmbeddingBackend(
        function=cast(Any, SentenceTransformerEmbedding(cast(SupportsEncode, model))),
        model_name="all-MiniLM-L6-v2 (384d)",
        label="内置轻量语义模型",
        is_bge_m3=False,
    )


def _try_bge_m3(settings: Settings) -> EmbeddingBackend | None:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    local_path = settings.embedding_model_path.strip()
    if local_path and not Path(local_path).is_dir():
        logger.warning("BGE-M3 本地模型目录不存在: {}", local_path)
        return None
    model_ref = local_path or settings.embedding_model
    try:
        model = SentenceTransformer(
            model_ref,
            device=settings.embedding_device,
            # Never trigger a multi-GB download implicitly at server start:
            # the model must be pre-downloaded to the HF cache or a local path.
            local_files_only=True,
        )
    except Exception as exc:
        logger.warning(
            "BGE-M3 模型加载失败 ({}): {} - {}",
            model_ref,
            type(exc).__name__,
            str(exc)[:300],
        )
        return None
    return EmbeddingBackend(
        function=cast(Any, SentenceTransformerEmbedding(cast(SupportsEncode, model))),
        model_name=f"BGE-M3 ({model_ref})",
        label="本地语义模型 BGE-M3",
        is_bge_m3=True,
    )


def _try_bge_m3_sparse(settings: Settings) -> EmbeddingBackend | None:
    """FlagEmbedding BGE-M3 双输出路径：稠密向量 + lexical 稀疏权重。

    依赖 ``FlagEmbedding`` 包与本地 BGE-M3 模型文件（与 reranker 共用的
    可选依赖）；任一缺失返回 ``None``，由工厂回退 sentence-transformers
    纯稠密路径。绝不触发隐式下载。
    """
    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError:
        logger.info("FlagEmbedding 未安装，稀疏检索不可用，使用纯稠密召回")
        return None
    local_path = settings.embedding_model_path.strip()
    if local_path and not Path(local_path).is_dir():
        logger.warning("BGE-M3 本地模型目录不存在: {}", local_path)
        return None
    model_ref = local_path or settings.embedding_model
    try:
        model = _load_bgem3_flag_model(BGEM3FlagModel, model_ref, settings)
    except Exception as exc:
        logger.warning(
            "BGE-M3 (FlagEmbedding) 双输出加载失败 ({}): {} - {}",
            model_ref,
            type(exc).__name__,
            str(exc)[:300],
        )
        return None
    encoder = BGEM3SparseEmbedding(model)
    return EmbeddingBackend(
        function=cast(Any, encoder),
        model_name=f"BGE-M3 ({model_ref})",
        label="本地语义模型 BGE-M3（稠密+稀疏双路）",
        is_bge_m3=True,
        sparse_encoder=encoder,
    )


def _load_bgem3_flag_model(model_cls: Any, model_ref: str, settings: Settings) -> Any:
    """加载 BGEM3FlagModel，兼容 FlagEmbedding 1.2 (device) 与 1.3+ (devices) 参数名。"""
    parameters = inspect.signature(model_cls.__init__).parameters
    kwargs: dict[str, Any] = {"use_fp16": False}
    if "devices" in parameters:
        kwargs["devices"] = [settings.embedding_device]
    elif "device" in parameters:
        kwargs["device"] = settings.embedding_device
    if "local_files_only" in parameters:
        kwargs["local_files_only"] = True
    return model_cls(model_ref, **kwargs)
