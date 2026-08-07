from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


class DocumentParseError(ValueError):
    """Raised when an uploaded knowledge document cannot be parsed."""


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    name: str
    content: str


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
        return ParsedDocument(name=name, content=content)

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
