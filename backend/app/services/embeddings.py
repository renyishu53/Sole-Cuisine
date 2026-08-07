"""Embedding backend factory with BGE-M3 support and graceful fallback.

The Chroma collection is bound to a single embedding function, so the backend
is resolved once per process and cached by the caller. BGE-M3 is an optional
dependency: when ``sentence-transformers`` or the local model files are
missing, the store falls back to Chroma's built-in ONNX MiniLM model.
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


class SentenceTransformerEmbedding:
    """Chroma-compatible adapter around a SentenceTransformer model."""

    def __init__(self, model: SupportsEncode) -> None:
        self._model = model

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors = self._model.encode(list(input), normalize_embeddings=True)
        return [_to_list(vector) for vector in vectors]

    @staticmethod
    def name() -> str:
        return "bge-m3-sentence-transformers"


def _to_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        return cast(list[float], vector.tolist())
    return [float(value) for value in vector]


def create_embedding_backend(settings: Settings) -> EmbeddingBackend:
    """Resolve the embedding backend, falling back to the built-in model."""
    provider = settings.embedding_provider.strip().lower()
    if provider in {"auto", "bge-m3", "bge_m3"}:
        backend = _try_bge_m3(settings)
        if backend is not None:
            return backend
        if provider != "auto":
            logger.warning("BGE-M3 向量模型不可用，回退到内置轻量模型")
    return _default_backend()


def _default_backend() -> EmbeddingBackend:
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return EmbeddingBackend(
        function=cast(Any, DefaultEmbeddingFunction()),
        model_name="Chroma DefaultEmbeddingFunction (ONNX MiniLM-L6-v2)",
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
        logger.warning("BGE-M3 模型加载失败 ({}): {}", model_ref, type(exc).__name__)
        return None
    return EmbeddingBackend(
        function=cast(Any, SentenceTransformerEmbedding(cast(SupportsEncode, model))),
        model_name=f"BGE-M3 ({model_ref})",
        label="本地语义模型 BGE-M3",
        is_bge_m3=True,
    )
