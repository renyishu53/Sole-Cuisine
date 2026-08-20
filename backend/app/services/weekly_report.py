"""Weekly report service.

Aggregates nutrition, budget, and execution data from the selected natural week,
输出 :class:`~app.schemas.domain.WeeklyReportResponse`。

建议由确定性规则引擎生成，保证 demo 模式（无 LLM）下同样可用；LLM 润色可作为
在规则建议之上叠加的增强层，不阻断报告主链路。
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NutritionGoal
from app.repositories.domain import DomainRepository
from app.repositories.planning import PlanningRepository, current_week_start_utc
from app.schemas.domain import (
    CoverageStats,
    WeeklyAchievement,
    WeeklyReportResponse,
    WeeklyReportPeriod,
    WeeklySuggestion,
)
from app.services.nutrition import build_nutrition_report, nutrition_goal_to_targets

_PRODUCT_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _current_week_bounds() -> tuple[datetime, datetime, str]:
    """Return the current natural week in the product timezone."""
    start = current_week_start_utc()
    end = start + timedelta(days=7)
    local_start = start.astimezone(_PRODUCT_TIMEZONE).date()
    iso = local_start.isocalendar()
    return start, end, f"{iso[0]}-W{iso[1]:02d}"


def _week_bounds_for_date(value: date) -> tuple[datetime, datetime, str]:
    """Return natural-week bounds for a local calendar date."""
    monday = value - timedelta(days=value.weekday())
    local_start = datetime.combine(monday, time.min, tzinfo=_PRODUCT_TIMEZONE)
    start = local_start.astimezone(UTC)
    end = start + timedelta(days=7)
    iso = monday.isocalendar()
    return start, end, f"{iso[0]}-W{iso[1]:02d}"


class WeeklyReportService:
    async def build_report(
        self, session: AsyncSession, user_id: int, week_start: date | None = None
    ) -> WeeklyReportResponse:
        planning = PlanningRepository(session)
        domain = DomainRepository(session)

        week_start_at, week_end, week_label = (
            _week_bounds_for_date(week_start) if week_start else _current_week_bounds()
        )
        plan = await planning.get_plan_for_period(
            user_id, start=week_start_at, end=week_end
        )
        if plan is None:
            return WeeklyReportResponse(
                has_data=False,
                week_start=week_start_at.astimezone(_PRODUCT_TIMEZONE).date().isoformat(),
                week_end=(week_end - timedelta(days=1))
                .astimezone(_PRODUCT_TIMEZONE)
                .date()
                .isoformat(),
                week_label=week_label,
            )

        meals = list(plan.meals)
        shopping = list(plan.shopping_items)

        # 1. 营养达成率只累计已吃餐食，目标按报告周期已过去的天数累计。
        #    历史周采用完整 7 天目标，本周采用截至今天的累计目标。
        goal = await session.scalar(
            select(NutritionGoal).where(NutritionGoal.user_id == user_id)
        )
        base_targets = nutrition_goal_to_targets(goal) if goal is not None else None
        local_start = week_start_at.astimezone(_PRODUCT_TIMEZONE).date()
        today = datetime.now(_PRODUCT_TIMEZONE).date()
        elapsed_days = min(7, max(1, (today - local_start).days + 1))
        period_targets = (
            {key: value * elapsed_days for key, value in base_targets.items()}
            if base_targets is not None
            else None
        )
        recipes = await domain.list_recipes(user_id)
        eaten_meals = [meal for meal in meals if meal.eaten]
        nutrition_report = build_nutrition_report(eaten_meals, recipes, period_targets)
        nutrition_percent = round(nutrition_report.overall_percent, 1)
        has_nutrition_data = bool(eaten_meals)

        # 2. 预算控制：与 budget_analytics 同口径（结余占比越高越好）
        expenses = await domain.list_expenses_filtered(
            user_id, start_date=week_start_at, end_date=week_end - timedelta(microseconds=1)
        )
        limit = plan.budget
        actual = round(sum(item.amount for item in expenses), 2)
        usage = round(actual / limit * 100) if limit else 0
        budget_usage = min(100, usage)

        # 3. 覆盖：餐食打卡 + 采购核销 的合计执行率
        meal_eaten = sum(1 for meal in meals if meal.eaten)
        shopping_purchased = sum(1 for item in shopping if item.purchased)
        total_units = len(meals) + len(shopping)
        coverage_percent = (
            round((meal_eaten + shopping_purchased) / total_units * 100, 1)
            if total_units
            else 0.0
        )

        achievements = [
            WeeklyAchievement(
                key="nutrition",
                label="营养达成",
                percent=nutrition_percent,
                detail=(
                    f"基于 {len(eaten_meals)} 餐已吃记录，目标为 {elapsed_days} 天累计目标；"
                    f"{nutrition_report.calibrated_meals} 餐命中菜谱校准"
                    if has_nutrition_data
                    else "暂无已吃记录；营养达成仅在餐食打卡后计算"
                ),
                has_data=has_nutrition_data,
            ),
            WeeklyAchievement(
                key="budget",
                label="预算使用",
                percent=budget_usage,
                detail=(
                    f"已用 ¥{actual:g} / ¥{limit:g}（{usage}%），"
                    f"结余 ¥{round(limit - actual, 2):g}"
                ),
            ),
            WeeklyAchievement(
                key="coverage",
                label="执行覆盖",
                percent=coverage_percent,
                detail=f"已吃 {meal_eaten}/{len(meals)} 餐 · 已购 {shopping_purchased}/{len(shopping)} 项",
            ),
        ]

        coverage = CoverageStats(
            meal_planned=len(meals),
            meal_eaten=meal_eaten,
            shopping_planned=len(shopping),
            shopping_purchased=shopping_purchased,
            coverage_percent=coverage_percent,
        )

        # 4. 行动建议只用于已结束且有实际执行记录的周。
        #    本周尚未完成时，不能把 0/21 等起始状态误解为用户执行不佳，
        #    更不能提前生成“下周”建议。
        report_finished = today >= local_start + timedelta(days=7)
        has_execution_data = bool(meal_eaten or shopping_purchased or actual)
        suggestions = (
            self._build_suggestions(
                nutrition_report=nutrition_report,
                has_nutrition_data=has_nutrition_data,
                usage=usage,
                actual=actual,
                limit=limit,
                meal_count=len(meals),
                meal_eaten=meal_eaten,
            )
            if report_finished and has_execution_data
            else []
        )

        return WeeklyReportResponse(
            has_data=True,
            week_start=week_start_at.astimezone(_PRODUCT_TIMEZONE).date().isoformat(),
            week_end=(week_end - timedelta(days=1))
            .astimezone(_PRODUCT_TIMEZONE)
            .date()
            .isoformat(),
            week_label=week_label,
            achievements=achievements,
            coverage=coverage,
            suggestions=suggestions,
            notices=[],
        )

    async def list_periods(
        self, session: AsyncSession, user_id: int
    ) -> list[WeeklyReportPeriod]:
        """List reportable plan weeks, newest first, without duplicate revisions."""
        timestamps = await PlanningRepository(session).list_confirmed_plan_dates(user_id)
        current_start = current_week_start_utc().astimezone(_PRODUCT_TIMEZONE).date()
        weeks: set[date] = {current_start}
        for timestamp in timestamps:
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            local_date = timestamp.astimezone(_PRODUCT_TIMEZONE).date()
            weeks.add(local_date - timedelta(days=local_date.weekday()))
        periods: list[WeeklyReportPeriod] = []
        for start_date in sorted(weeks, reverse=True):
            start, end, label = _week_bounds_for_date(start_date)
            periods.append(
                WeeklyReportPeriod(
                    week_start=start.astimezone(_PRODUCT_TIMEZONE).date().isoformat(),
                    week_end=(end - timedelta(days=1))
                    .astimezone(_PRODUCT_TIMEZONE)
                    .date()
                    .isoformat(),
                    week_label=label,
                )
            )
        return periods

    @staticmethod
    def _build_suggestions(
        *,
        nutrition_report,
        has_nutrition_data: bool,
        usage: int,
        actual: float,
        limit: float,
        meal_count: int,
        meal_eaten: int,
    ) -> list[WeeklySuggestion]:
        suggestions: list[WeeklySuggestion] = []

        calories = nutrition_report.nutrients.get("calories")
        protein = nutrition_report.nutrients.get("protein_g")
        if has_nutrition_data and calories is not None and calories.percent < 60:
            suggestions.append(
                WeeklySuggestion(
                    category="nutrition",
                    title="热量摄入偏低",
                    detail=f"本周热量达成率仅 {round(calories.percent)}%（目标 {calories.target:g} kcal）",
                    action="早餐增加蛋奶或坚果，把每日热量缺口补足到 80% 以上",
                )
            )
        if has_nutrition_data and protein is not None and protein.percent < 60:
            suggestions.append(
                WeeklySuggestion(
                    category="nutrition",
                    title="蛋白质摄入不足",
                    detail=f"本周蛋白质达成率仅 {round(protein.percent)}%",
                    action="午餐把素菜换成鸡胸肉、鸡蛋或豆腐，提高单餐蛋白密度",
                )
            )

        if limit and usage >= 85:
            suggestions.append(
                WeeklySuggestion(
                    category="budget",
                    title="预算接近上限",
                    detail=f"本周已用 ¥{actual:g} / ¥{limit:g}（{usage}%）",
                    action="下周把高价海鲜替换为鸡胸肉或豆腐，单餐成本控制在 ¥30 以内",
                )
            )
        elif limit and actual > 0 and usage <= 60:
            suggestions.append(
                WeeklySuggestion(
                    category="budget",
                    title="预算结余充足",
                    detail=f"本周仅用 {usage}%，结余 ¥{round(limit - actual, 2):g}",
                    action="把结余用于升级蛋白质来源（如三文鱼/牛里脊），提升营养密度",
                )
            )

        if meal_count and meal_eaten / meal_count < 0.5:
            suggestions.append(
                WeeklySuggestion(
                    category="coverage",
                    title="餐食打卡覆盖率偏低",
                    detail=f"本周仅打卡 {meal_eaten}/{meal_count} 餐",
                    action="把周三设为快手菜日（20 分钟内），降低执行阻力、提高打卡率",
                )
            )

        if not suggestions:
            suggestions.append(
                WeeklySuggestion(
                    category="coverage",
                    title="保持当前节奏",
                    detail="本周各项指标表现平稳",
                    action="继续按计划执行，完成每日餐食打卡以累积更多偏好数据",
                )
            )
        if len(suggestions) < 2:
            suggestions.append(
                WeeklySuggestion(
                    category="coverage",
                    title="完善打卡习惯",
                    detail="打卡与反馈是下一轮规划更精准的关键输入",
                    action="每餐后顺手点「打卡已吃」或记录未吃原因，形成完整执行记录",
                )
            )
        return suggestions


weekly_report_service = WeeklyReportService()
