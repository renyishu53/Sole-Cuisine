"""P3 Planner 分段生成测试。

验证 SegmentedPlanGenerator 的核心行为：
1. 三阶段顺序执行（meals → shopping → budget）
2. 每阶段 JSON 解析与 schema 校验
3. 任一阶段失败时回退到单次生成模式
4. settings 开关控制是否启用分段

不依赖真实 LLM 调用——用 StreamingModel stub 模拟流式响应。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.ai.llm import PlanDraft
from app.ai.segmented_planner import (
    BudgetStage,
    MealsStage,
    SegmentedPlanGenerator,
    ShoppingStage,
)
from app.core.config import Settings
from app.schemas import PlanningRequest
from app.schemas.domain import BudgetSummary, MealItem, ShoppingItem

# ── 流式模型 stub ────────────────────────────────────────────────────────


class Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class StreamingModel:
    """按预设响应队列模拟流式 LLM 调用。

    每次调用 astream 消费队列中下一个响应，模拟分段生成中
    meals/shopping/budget 三阶段各自的 LLM 输出。
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._call_index = 0
        self.calls: list[tuple[str, str]] = []  # (system, user) 记录

    async def astream(self, messages: object) -> Any:
        messages_list = list(messages)  # type: ignore[arg-type]
        system = messages_list[0][1] if messages_list else ""
        user = messages_list[1][1] if len(messages_list) > 1 else ""
        self.calls.append((system, user))

        idx = self._call_index
        self._call_index += 1
        if idx >= len(self._responses):
            raise RuntimeError(f"StreamingModel 预设响应已用完 (call #{idx})")

        response = self._responses[idx]
        # 按 31 字符分块模拟流式
        for offset in range(0, len(response), 31):
            yield Chunk(response[offset : offset + 31])


class FailingStreamingModel:
    """模拟 LLM 调用失败的模型。"""

    async def astream(self, messages: object) -> Any:
        raise ConnectionError("LLM 服务不可用")
        yield  # 让 Python 识别为 async generator


# ── 测试数据 ─────────────────────────────────────────────────────────────


def _make_meals_json() -> str:
    meals = [
        MealItem(
            id=i,
            day=day,
            name=f"测试菜品{i}",
            duration=20,
            cost=30,
            tags=["快手"],
            reason="测试",
            ingredients=["鸡肉", "米饭"],
        )
        for i, day in enumerate(["周一", "周二", "周三", "周四", "周五", "周六", "周日"])
    ]
    return json.dumps(
        {"summary": "分段生成测试菜单", "meals": [m.model_dump(mode="json") for m in meals]},
        ensure_ascii=False,
    )


def _make_shopping_json() -> str:
    items = [
        ShoppingItem(
            id=i,
            name=name,
            category="测试",
            quantity="500g",
            price=20,
            source="分段生成",
            purchased=False,
        )
        for i, name in enumerate(["鸡肉", "米饭", "蔬菜"])
    ]
    return json.dumps(
        {"shopping": [item.model_dump(mode="json") for item in items]},
        ensure_ascii=False,
    )


def _make_budget_json() -> str:
    return json.dumps(
        {
            "budget": {
                "limit": 500,
                "estimated": 350,
                "saved": 150,
                "usage_percent": 70,
                "categories": {"肉蛋奶": 200, "蔬菜": 100, "主食": 50},
            }
        },
        ensure_ascii=False,
    )


def _make_settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="test-key",
        planner_segmented_enabled=True,
    )


# ── 三阶段顺序执行 ───────────────────────────────────────────────────────


def test_segmented_generator_implements_plan_generator_protocol():
    """SegmentedPlanGenerator 应实现 PlanGenerator 协议。"""
    generator = SegmentedPlanGenerator(_make_settings())
    assert hasattr(generator, "mode")
    assert hasattr(generator, "generate")
    assert "segmented" in generator.mode


def test_segmented_generator_three_stages_produce_complete_draft():
    """三阶段顺序执行应产出完整的 PlanDraft。"""
    settings = _make_settings()
    generator = SegmentedPlanGenerator(settings)
    generator._model = StreamingModel(  # type: ignore[assignment]
        [_make_meals_json(), _make_shopping_json(), _make_budget_json()]
    )

    request = PlanningRequest(prompt="一周晚餐规划", budget=500)
    draft = asyncio.run(generator.generate(request, "测试上下文"))

    assert isinstance(draft, PlanDraft)
    assert draft.summary == "分段生成测试菜单"
    assert len(draft.meals) == 7
    assert draft.meals[0].name == "测试菜品0"
    assert len(draft.shopping) == 3
    assert draft.shopping[0].name == "鸡肉"
    assert draft.budget.limit == 500
    assert draft.budget.estimated == 350
    # 分段生成的 suggestions 应包含阶段说明
    assert any("分段生成" in s for s in draft.suggestions)


def test_segmented_generator_calls_model_three_times():
    """分段生成应调用 LLM 三次（meals/shopping/budget 各一次）。"""
    settings = _make_settings()
    generator = SegmentedPlanGenerator(settings)
    model = StreamingModel(
        [_make_meals_json(), _make_shopping_json(), _make_budget_json()]
    )
    generator._model = model  # type: ignore[assignment]

    request = PlanningRequest(prompt="一周晚餐规划", budget=500)
    asyncio.run(generator.generate(request, "上下文"))

    assert len(model.calls) == 3
    # 验证每阶段的 system prompt 含对应角色
    assert "餐食规划" in model.calls[0][0]
    assert "采购规划" in model.calls[1][0]
    assert "预算规划" in model.calls[2][0]
    # shopping 阶段的 user prompt 应包含 meals 的 JSON
    assert "鸡肉" in model.calls[1][1]
    # budget 阶段的 user prompt 应包含 shopping 的 JSON
    assert "500" in model.calls[2][1]


# ── 阶段失败回退 ─────────────────────────────────────────────────────────


def test_segmented_generator_falls_back_on_stage_failure():
    """任一阶段 LLM 调用失败时应回退到单次生成模式。"""
    settings = _make_settings()
    generator = SegmentedPlanGenerator(settings)
    # meals 阶段就失败
    generator._model = FailingStreamingModel()  # type: ignore[assignment]

    # 回退的 fallback 也用 stub
    class StubFallback:
        @property
        def mode(self) -> str:
            return "fallback"

        async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
            return PlanDraft(
                summary="回退生成",
                meals=[],
                shopping=[],
                tasks=[],
                budget=BudgetSummary(
                    limit=500, estimated=0, saved=500, usage_percent=0, categories={}
                ),
                conflicts=[],
                suggestions=["分段失败，已回退"],
            )

    generator._fallback = StubFallback()  # type: ignore[assignment]

    request = PlanningRequest(prompt="一周晚餐规划", budget=500)
    draft = asyncio.run(generator.generate(request, "上下文"))

    assert draft.summary == "回退生成"
    assert "分段失败" in draft.suggestions[0]


def test_segmented_generator_falls_back_on_invalid_json():
    """阶段返回无效 JSON 时应回退到单次生成模式。"""
    settings = _make_settings()
    generator = SegmentedPlanGenerator(settings)
    generator._model = StreamingModel(["这不是JSON"])  # type: ignore[assignment]

    class StubFallback:
        @property
        def mode(self) -> str:
            return "fallback"

        async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
            return PlanDraft(
                summary="JSON 解析失败回退",
                meals=[],
                shopping=[],
                tasks=[],
                budget=BudgetSummary(
                    limit=500, estimated=0, saved=500, usage_percent=0, categories={}
                ),
                conflicts=[],
                suggestions=[],
            )

    generator._fallback = StubFallback()  # type: ignore[assignment]

    request = PlanningRequest(prompt="一周晚餐规划", budget=500)
    draft = asyncio.run(generator.generate(request, "上下文"))

    assert draft.summary == "JSON 解析失败回退"


def test_segmented_generator_falls_back_on_schema_mismatch():
    """阶段返回的 JSON 不符合 schema 时应回退到单次生成模式。"""
    settings = _make_settings()
    generator = SegmentedPlanGenerator(settings)
    # meals 阶段返回的 JSON 缺少必需字段
    generator._model = StreamingModel(['{"summary": "缺少 meals 字段"}'])  # type: ignore[assignment]

    class StubFallback:
        @property
        def mode(self) -> str:
            return "fallback"

        async def generate(self, request: PlanningRequest, context: str) -> PlanDraft:
            return PlanDraft(
                summary="Schema 校验失败回退",
                meals=[],
                shopping=[],
                tasks=[],
                budget=BudgetSummary(
                    limit=500, estimated=0, saved=500, usage_percent=0, categories={}
                ),
                conflicts=[],
                suggestions=[],
            )

    generator._fallback = StubFallback()  # type: ignore[assignment]

    request = PlanningRequest(prompt="一周晚餐规划", budget=500)
    draft = asyncio.run(generator.generate(request, "上下文"))

    assert draft.summary == "Schema 校验失败回退"


# ── settings 开关 ────────────────────────────────────────────────────────


def test_build_plan_generator_uses_segmented_when_enabled():
    """planner_segmented_enabled=True 且真实 LLM 启用时应返回 SegmentedPlanGenerator。"""
    from app.ai.llm import build_plan_generator

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="test-key",
        planner_segmented_enabled=True,
    )
    generator = build_plan_generator(settings)
    assert isinstance(generator, SegmentedPlanGenerator)


def test_build_plan_generator_uses_single_when_disabled():
    """planner_segmented_enabled=False 时应返回单次生成器。"""
    from app.ai.llm import OpenAICompatiblePlanGenerator, build_plan_generator

    settings = Settings(
        _env_file=None,
        llm_provider="deepseek",
        llm_api_key="test-key",
        planner_segmented_enabled=False,
    )
    generator = build_plan_generator(settings)
    assert isinstance(generator, OpenAICompatiblePlanGenerator)
    assert not isinstance(generator, SegmentedPlanGenerator)


def test_build_plan_generator_uses_demo_when_no_real_llm():
    """无真实 LLM 配置时应返回 DemoPlanGenerator，即使分段开关开启。"""
    from app.ai.llm import DemoPlanGenerator, build_plan_generator

    settings = Settings(
        _env_file=None,
        llm_provider="demo",
        planner_segmented_enabled=True,
    )
    generator = build_plan_generator(settings)
    assert isinstance(generator, DemoPlanGenerator)


# ── 中间结果 schema 校验 ─────────────────────────────────────────────────


def test_meals_stage_schema_validates_correct_data():
    """MealsStage 应正确校验含 7 餐的菜单数据。"""
    data = json.loads(_make_meals_json())
    stage = MealsStage.model_validate(data)
    assert stage.summary == "分段生成测试菜单"
    assert len(stage.meals) == 7
    assert all(isinstance(m, MealItem) for m in stage.meals)


def test_shopping_stage_schema_validates_correct_data():
    """ShoppingStage 应正确校验采购清单数据。"""
    data = json.loads(_make_shopping_json())
    stage = ShoppingStage.model_validate(data)
    assert len(stage.shopping) == 3
    assert all(isinstance(s, ShoppingItem) for s in stage.shopping)


def test_budget_stage_schema_validates_correct_data():
    """BudgetStage 应正确校验预算分配数据。"""
    data = json.loads(_make_budget_json())
    stage = BudgetStage.model_validate(data)
    assert isinstance(stage.budget, BudgetSummary)
    assert stage.budget.limit == 500
