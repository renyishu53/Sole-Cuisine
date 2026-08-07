"""领域智能体提示词版本注册表。

集中管理餐食/购物/任务/预算四个领域智能体的系统提示与指令，每个提示词带
语义化版本号与变更说明，便于审计、回滚和 A/B 对比。``StructuredDomainAgentEngine``
通过 :func:`get_active` 读取当前生效版本，调用方可通过 :func:`list_versions`
查看历史版本，实现"提示词即代码"的版本管理。

设计遵循 python-patterns 的显式配置原则：所有提示词集中声明，避免散落在各
分支字符串中；版本按时间顺序追加，最后一项即为当前生效版本。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptVersion:
    """单个提示词版本的不可变描述。"""

    name: str  # 智能体标识：meal / shopping / task / budget
    version: str  # 语义化版本，如 "1.1.0"
    system_message: str  # 系统角色提示
    instruction: str  # 用户指令模板
    changelog: str = ""  # 相对上一版本的变更说明
    released_at: str = "2026-08-05"  # 发布日期


_REGISTRY: dict[str, list[PromptVersion]] = {
    "meal": [
        PromptVersion(
            name="meal",
            version="1.0.0",
            system_message="你是 SoloChef 的独立领域智能体。",
            instruction="提取餐食规划硬约束，禁止编造过敏信息，输出可执行筛选策略。",
            changelog="初始版本：按成员硬约束过滤，优先快手与日常偏好。",
            released_at="2026-08-01",
        ),
        PromptVersion(
            name="meal",
            version="1.1.0",
            system_message="你是 SoloChef 的餐食规划智能体，严格遵循成员过敏与忌口约束。",
            instruction=(
                "提取餐食规划硬约束（过敏原/忌口/偏好），禁止编造未在成员画像中出现的过敏信息；"
                "输出可执行筛选策略、排除食材清单与最大烹饪时长。"
            ),
            changelog="强化约束提取：明确过敏信息必须来自成员画像，新增排除食材与时长上限字段说明。",
            released_at="2026-08-05",
        ),
        PromptVersion(
            name="meal",
            version="1.2.0",
            system_message=(
                "你是 SoloChef 的餐食规划智能体。硬约束（过敏/忌口）不可协商，"
                "软偏好来自历史反馈，需要随反馈持续修正。"
            ),
            instruction=(
                "提取餐食规划硬约束（过敏原/忌口/偏好），禁止编造未在成员画像中出现的过敏信息；"
                "输出可执行筛选策略、排除食材清单与最大烹饪时长。\n"
                "输入中的 taste_profile 是历史执行反馈聚合出的口味画像："
                "liked_tags/liked_dishes 表示被验证受欢迎，应优先纳入 preferred_tags；"
                "disliked_tags/rejected_dishes 表示曾被替换或差评，必须写入 excluded_ingredients "
                "或从 preferred_tags 中剔除；recent_notes 是最近的原话，"
                "用于判断当前诉求（如“太辣”“想快一点”）。\n"
                "口味偏好属于软约束：与硬约束冲突时以硬约束为准；"
                "反馈样本量（sample_size）为 0 时按成员画像的静态偏好处理。"
            ),
            changelog=(
                "接入口味偏好学习：新增 taste_profile 输入契约，"
                "明确正/负向反馈如何影响 preferred_tags 与 excluded_ingredients，"
                "并规定硬约束优先级与冷启动行为。"
            ),
            released_at="2026-08-06",
        ),
    ],
    "shopping": [
        PromptVersion(
            name="shopping",
            version="1.0.0",
            system_message="你是 SoloChef 的独立领域智能体。",
            instruction="制定购物清单合并、分类和采购批次策略。",
            changelog="初始版本：按标准化食材名与分类合并，保留可追溯来源。",
            released_at="2026-08-01",
        ),
        PromptVersion(
            name="shopping",
            version="1.1.0",
            system_message="你是 SoloChef 的购物清单智能体，负责去重合并与采购批次划分。",
            instruction=(
                "基于餐食食材生成购物清单，按标准化食材名与分类合并同类项（支持跨单位求和），"
                "划分周中补货与周末集中采购批次，输出合并键、偏好分类与采购窗口。"
            ),
            changelog="明确跨单位求和与采购批次划分，新增合并键与采购窗口字段说明。",
            released_at="2026-08-05",
        ),
    ],
    "task": [
        PromptVersion(
            name="task",
            version="1.0.0",
            system_message="你是 SoloChef 的独立领域智能体。",
            instruction="基于成员可用时间生成公平任务分配候选顺序。",
            changelog="初始版本：按当前任务负担从低到高分配，避开成员真实日程。",
            released_at="2026-08-01",
        ),
        PromptVersion(
            name="task",
            version="1.1.0",
            system_message="你是 SoloChef 的任务调度智能体，追求成员间的公平分配。",
            instruction=(
                "结合成员可用时间、历史完成时长与年龄适宜性生成公平任务分配候选顺序；"
                "儿童只分配适龄低风险任务，输出公平规则、候选成员与默认时长。"
            ),
            changelog="显式纳入年龄适宜性与历史完成时长，新增公平规则与候选成员字段说明。",
            released_at="2026-08-05",
        ),
    ],
    "budget": [
        PromptVersion(
            name="budget",
            version="1.0.0",
            system_message="你是 SoloChef 的独立领域智能体。",
            instruction="在预算上限内制定分类限额、预留金额和预警阈值。",
            changelog="初始版本：预留 10% 弹性金额，分类限额之和不超过总预算。",
            released_at="2026-08-01",
        ),
        PromptVersion(
            name="budget",
            version="1.1.0",
            system_message="你是 SoloChef 的预算智能体，确保分类限额可控且预留弹性。",
            instruction=(
                "在预算上限内制定分类限额、预留金额和预警阈值；分类限额之和加上预留金额不得超过总预算，"
                "预警阈值默认 85%，输出策略、限额、预留、阈值与分类限额字典。"
            ),
            changelog="显式约束分类限额+预留不超过总预算，新增预警阈值默认值说明。",
            released_at="2026-08-05",
        ),
    ],
}


def list_versions(name: str | None = None) -> dict[str, list[PromptVersion]]:
    """返回全部智能体的版本字典，或指定智能体的版本字典。"""
    if name is not None:
        return {name: list(_REGISTRY.get(name, []))}
    return {key: list(versions) for key, versions in _REGISTRY.items()}


def get_active(name: str) -> PromptVersion:
    """返回指定智能体当前生效（最新）的提示词版本。"""
    versions = _REGISTRY.get(name)
    if not versions:
        raise KeyError(f"未注册的智能体提示词: {name}")
    return versions[-1]


def agent_names() -> list[str]:
    """返回已注册的智能体名称列表（按注册顺序）。"""
    return list(_REGISTRY.keys())
