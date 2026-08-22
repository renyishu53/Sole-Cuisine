"""Embedding backend factory with BGE-M3 support and graceful fallback.

The Milvus collection is bound to a fixed vector dimension, so the backend
is resolved once per process and cached by the caller. BGE-M3 is an optional
dependency: when ``sentence-transformers`` or the local model files are
missing, the store falls back to a lightweight MiniLM model.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from loguru import logger

from app.core.config import Settings


class SupportsEncode(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class EmbeddingBackend:
    """Resolved embedding function plus display metadata."""

    function: Any
    model_name: str
    label: str
    is_bge_m3: bool

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to a list of float vectors (Milvus-compatible format)."""
        result = self.function(list(texts))
        # numpy ndarray → list[list[float]]
        if hasattr(result, "tolist"):
            return result.tolist()
        # Already iterable of vectors
        return [list(v) for v in result]


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


def create_embedding_backend(settings: Settings) -> EmbeddingBackend:
    """Resolve the embedding backend, falling back to the lightweight model."""
    provider = settings.embedding_provider.strip().lower()
    if provider in {"auto", "bge-m3", "bge_m3"}:
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
