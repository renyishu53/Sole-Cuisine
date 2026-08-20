"""多模态视觉识别服务：基于 Qwen-VL 的图片理解。

与 llm.py 中 ChatAssistant 的区别：
- 使用独立的 VLM 配置（vlm_*），不依赖 DeepSeek 文本 LLM
- 绑定 response_format=json_object，要求模型输出结构化 JSON
- 支持四种场景：食材识别 / 菜品+热量 / 营养表OCR / 小票OCR
- 图片预处理：长边压缩到 2048px + JPEG quality=85，控制 token 成本
"""

import asyncio
import base64
import io
import json

from langchain_openai import ChatOpenAI
from PIL import Image
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.schemas.domain import VisionScene


class VisionError(RuntimeError):
    """视觉识别失败（模型调用、JSON 解析、Schema 校验等）。"""


class ImageTooLargeError(ValueError):
    """图片超过配置的大小上限。"""


class ImageDecodeError(ValueError):
    """图片格式无法解码。"""


class ProcessedImage(BaseModel):
    """预处理后的图片 data URL，可直接传给 Qwen-VL。"""

    data_url: str
    mime: str
    size_kb: int


# 各场景的 system prompt —— 决定 Qwen-VL 如何理解图片并输出 JSON
SCENE_PROMPTS: dict[VisionScene, str] = {
    VisionScene.AUTO: (
        "你是饮食与购物图片识别助手。先判断图片最适合按食材、菜品热量、"
        "营养标签或购物小票中的哪一类理解，再输出对用户最有用的 JSON 对象："
        '{"summary":"一句话说明识别内容","items":[{"name":"条目名称",'
        '"quantity":"数量或份量（如适用）","category":"分类（如适用）",'
        '"value":"营养数值或价格（如适用）","unit":"单位（如适用）"}],'
        '"calories":估算总热量数值（仅菜品适用）,"raw_text":"可读文字（仅标签或小票适用）"}'
    ),
    VisionScene.INGREDIENT: (
        "你是食材识别专家。识别图片中的所有食材，只输出 JSON 对象："
        '{"summary":"一句话概述图片内容","items":['
        '{"name":"食材名","quantity":"估测量如200g或2个","category":"蔬菜|肉类|主食|调味料|其他"}'
        "]}"
    ),
    VisionScene.DISH: (
        "你是菜品识别与营养估算专家。识别图片中的菜品并估算热量。只输出 JSON 对象："
        '{"summary":"菜品名称与特征","items":['
        '{"name":"菜品名","portion":"份量估计","ingredients":["主要食材1","主要食材2"]}'
        '],"calories":估算总热量数值（千卡）}'
    ),
    VisionScene.NUTRITION_LABEL: (
        "你是食品营养标签 OCR 专家。提取图片中营养成分表的数据。只输出 JSON 对象："
        '{"summary":"食品名称","items":['
        '{"name":"能量","value":数值,"unit":"kJ或kcal"},'
        '{"name":"蛋白质","value":数值,"unit":"g"},'
        '{"name":"脂肪","value":数值,"unit":"g"},'
        '{"name":"碳水化合物","value":数值,"unit":"g"}'
        '],"raw_text":"完整原文文字"}'
    ),
    VisionScene.RECEIPT: (
        "你是购物小票 OCR 专家。提取图片中所有商品行和总金额。只输出 JSON 对象："
        '{"summary":"购物场所与日期","items":['
        '{"name":"商品名","price":数值,"quantity":数量}'
        '],"raw_text":"完整原文文字"}'
    ),
}


def preprocess_image(raw: bytes, settings: Settings) -> ProcessedImage:
    """图片预处理：解码 → 长边压缩 → JPEG 重编码 → base64 data URL。

    Qwen-VL 对图片尺寸敏感，长边超过 2048px 会显著增加 token 消耗和延迟，
    统一压缩到 2048px 内可平衡精度与成本。

    Raises:
        ImageTooLargeError: 图片超过 vlm_max_image_size MB
        ImageDecodeError: 图片格式无法解码
    """
    max_bytes = settings.vlm_max_image_size * 1024 * 1024
    if len(raw) > max_bytes:
        raise ImageTooLargeError(
            f"图片 {len(raw) // 1024}KB 超过 {settings.vlm_max_image_size}MB 限制"
        )
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ImageDecodeError(f"无法解码图片: {type(exc).__name__}") from exc

    max_dim = settings.vlm_max_image_dimension
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            Image.LANCZOS,
        )

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")

    return ProcessedImage(
        data_url=f"data:image/jpeg;base64,{encoded}",
        mime="image/jpeg",
        size_kb=len(buf.getvalue()) // 1024,
    )


class VisionService:
    """Qwen-VL 调用封装，与文本 LLM 链路解耦。

    遵循 llm.py 中 ChatAssistant 的设计模式：
    - 懒加载 ChatOpenAI 客户端
    - 绑定 response_format=json_object 保证结构化输出
    - 模块级单例 get_vision_service() 管理生命周期
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: ChatOpenAI | None = None

    def _get_model(self) -> ChatOpenAI:
        if self._model is None:
            self._model = ChatOpenAI(
                api_key=self._settings.vlm_api_key,
                base_url=self._settings.vlm_base_url,
                model=self._settings.vlm_model,
                temperature=0.1,
                timeout=self._settings.vlm_timeout_seconds,
                max_retries=1,
                max_tokens=2048,
            ).bind(response_format={"type": "json_object"})
        return self._model

    async def recognize(
        self, image: ProcessedImage, scene: VisionScene
    ) -> dict[str, object]:
        """单图识别入口，返回结构化 JSON 字典。

        Returns:
            包含 summary / items / calories / raw_text 等字段的字典。

        Raises:
            VisionError: 模型调用失败或返回非法 JSON。
        """
        model = self._get_model()
        system_prompt = SCENE_PROMPTS[scene]
        user_content: list[dict[str, object]] = [
            {"type": "text", "text": "请分析这张图片并按要求的 JSON 格式输出。"},
            {"type": "image_url", "image_url": {"url": image.data_url}},
        ]
        try:
            response = await model.ainvoke(
                [("system", system_prompt), ("user", user_content)]
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise VisionError(
                f"Qwen-VL 调用失败 ({type(exc).__name__}: {str(exc)[:300]})"
            ) from exc

        content = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise VisionError(
                f"Qwen-VL 返回非 JSON ({exc.msg}): {content[:200]}"
            ) from exc

        try:
            validated = VisionResultPayload.model_validate(payload)
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                for e in exc.errors()[:3]
            )
            raise VisionError(f"Qwen-VL 结果结构异常 ({errors})") from exc

        result: dict[str, object] = {
            "scene": scene.value,
            "summary": validated.summary,
            "items": validated.items,
            "calories": validated.calories,
            "raw_text": validated.raw_text,
        }
        return result


class VisionResultPayload(BaseModel):
    """Qwen-VL 返回 JSON 的内部校验模型。"""

    summary: str = ""
    items: list[dict[str, object]] = []
    calories: float | None = None
    raw_text: str = ""


_vision_service: VisionService | None = None


def get_vision_service() -> VisionService | None:
    """懒加载视觉服务单例。未配置 VLM 时返回 None，调用方需做 None 检查。"""
    global _vision_service
    if _vision_service is None:
        settings = get_settings()
        if settings.vlm_enabled_real:
            _vision_service = VisionService(settings)
    return _vision_service
