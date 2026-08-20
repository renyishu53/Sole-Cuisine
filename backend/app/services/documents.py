from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


class DocumentParseError(ValueError):
    """Raised when an uploaded knowledge document cannot be parsed."""


# ── 文档元数据（frontmatter → Milvus 标量过滤字段）────────────────────────
# goal_type 复用营养目标的英文词表 bulk/cut/maintain，保证规划侧传入的
# ``UserProfile.goal_type`` 能直接与入库元数据对齐过滤。
METADATA_FIELDS: tuple[str, ...] = ("goal_type", "meal_time", "allergens", "nutrition_focus")

DEFAULT_METADATA: dict[str, str] = {
    "goal_type": "maintain",  # 通用/健康维护，作为过滤时的兜底桶
    "meal_time": "通用",
    "allergens": "",
    "nutrition_focus": "均衡",
}

# 中文 frontmatter 标签 → 元数据字段名
_FRONTMATTER_FIELD_MAP: dict[str, str] = {
    "目标取向": "goal_type",
    "适用餐次": "meal_time",
    "禁忌过敏": "allergens",
    "营养侧重": "nutrition_focus",
}

# 目标取向中文取值 → goal_type 英文词表（对齐 UserProfile.goal_type）
_GOAL_TYPE_VALUES: dict[str, str] = {
    "增肌": "bulk",
    "减脂": "cut",
    "健康维护": "maintain",
    "通用": "maintain",
}


def parse_frontmatter(content: str) -> tuple[str, dict[str, str]]:
    """解析文档头部 ``---`` 围栏内的 frontmatter 元数据。

    返回 ``(剥离元数据后的正文, 元数据字典)``。无 frontmatter 时正文原样返回，
    元数据回退默认值，保证旧文档仍可入库。
    """
    metadata = dict(DEFAULT_METADATA)
    body = content
    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) >= 3:
            raw_meta, body = parts[1], parts[2]
            for raw_line in raw_meta.splitlines():
                line = raw_line.replace("：", ":").strip()
                if not line or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key, value = key.strip(), value.strip()
                field_name = _FRONTMATTER_FIELD_MAP.get(key)
                if field_name is None or not value:
                    continue
                if field_name == "goal_type":
                    value = _GOAL_TYPE_VALUES.get(value, DEFAULT_METADATA["goal_type"])
                metadata[field_name] = value
    return body.strip(), metadata


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    name: str
    content: str
    metadata: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_METADATA))


class DocumentProcessor:
    SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}

    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n## ", "\n### ", "\n\n", "\n", "。", "；", "，", " "],
        )

    def parse(self, name: str, payload: bytes) -> ParsedDocument:
        suffix = Path(name).suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            supported = ", ".join(sorted(self.SUPPORTED_SUFFIXES))
            raise DocumentParseError(f"不支持 {suffix or '无扩展名'} 文件，仅支持 {supported}")
        content = self._parse_pdf(payload) if suffix == ".pdf" else self._decode_text(payload)
        content = content.strip()
        if len(content) < 20:
            raise DocumentParseError("文档没有足够的可索引文本")
        body, metadata = parse_frontmatter(content)
        if len(body) < 20:
            raise DocumentParseError("文档没有足够的可索引文本")
        return ParsedDocument(name=name, content=body, metadata=metadata)

    def split(self, content: str) -> list[str]:
        return [chunk.strip() for chunk in self._splitter.split_text(content) if chunk.strip()]

    @staticmethod
    def _decode_text(payload: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise DocumentParseError("文本编码无法识别，请转换为 UTF-8")

    @staticmethod
    def _parse_pdf(payload: bytes) -> str:
        try:
            pages = PdfReader(BytesIO(payload)).pages
            return "\n\n".join(page.extract_text() or "" for page in pages)
        except Exception as exc:
            raise DocumentParseError("PDF 解析失败，可能是扫描件或文件已损坏") from exc
