"""阶段1 生活约束（备餐时间/厨具）→ 餐食智能体 的单元测试。

这些测试只验证确定性回退路径（``use_llm=False``），不触发任何网络请求，
聚焦 prep_time_max → max_duration_minutes 的映射与 kitchenware 硬约束注入。
"""
import pytest

from app.ai.domain_agents import StructuredDomainAgentEngine
from app.core.config import Settings
from app.schemas import PlanningRequest


@pytest.fixture
def engine() -> StructuredDomainAgentEngine:
    """无 LLM 的领域智能体，走确定性回退，保证测试无网络依赖。"""
    settings = Settings(_env_file=None, llm_provider="demo", llm_api_key="")
    return StructuredDomainAgentEngine(settings, use_llm=False)


@pytest.fixture
def plan_request() -> PlanningRequest:
    return PlanningRequest(prompt="想吃得健康一点", budget=600)


@pytest.mark.asyncio
async def test_meal_agent_maps_prep_time_max_to_max_duration(
    engine: StructuredDomainAgentEngine, plan_request: PlanningRequest
) -> None:
    """prep_time_max=20 直接决定 max_duration_minutes=20。"""
    result, mode, _err = await engine.meal(plan_request, prep_time_max=20)
    assert mode.startswith("deterministic")
    assert result.max_duration_minutes == 20


@pytest.mark.asyncio
async def test_meal_agent_default_duration_without_prep_time(
    engine: StructuredDomainAgentEngine, plan_request: PlanningRequest
) -> None:
    """未提供 prep_time_max 时回退到启发式 25/40 分钟。"""
    result, _mode, _err = await engine.meal(plan_request)
    assert result.max_duration_minutes in (25, 40)


@pytest.mark.asyncio
async def test_meal_agent_surfaces_kitchenware_as_constraint(
    engine: StructuredDomainAgentEngine, plan_request: PlanningRequest
) -> None:
    """kitchenware 进入硬约束，供规划器排除需要清单外厨具的菜式。"""
    result, _mode, _err = await engine.meal(
        plan_request, kitchenware=["炒锅", "电饭煲"]
    )
    assert any("仅使用厨具" in c for c in result.constraints_applied)


@pytest.mark.asyncio
async def test_meal_agent_clamps_prep_time_range(
    engine: StructuredDomainAgentEngine, plan_request: PlanningRequest
) -> None:
    """prep_time_max 越界时收敛到 [5, 240]。"""
    low, _mode, _err = await engine.meal(plan_request, prep_time_max=1)
    high, _mode, _err = await engine.meal(plan_request, prep_time_max=999)
    assert low.max_duration_minutes == 5
    assert high.max_duration_minutes == 240
