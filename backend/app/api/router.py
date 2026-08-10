from base64 import b64encode
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ai.evaluation import evaluate_plan
from app.ai.llm import LLMGenerationError, smoke_test_llm
from app.ai.prompts import agent_names, get_active, list_versions
from app.api.dependencies import CurrentContext, OwnerContext, SessionDep
from app.core.config import get_settings
from app.models import (
    NutritionGoal,
    RecipeRecord,
    UserProfile,
    WeeklyPlan,
)
from app.repositories import (
    BackgroundJobRepository,
    ConversationRepository,
    DomainRepository,
    FeedbackRepository,
    PlanningRepository,
)
from app.schemas import (
    AgentRun,
    AIServiceStatus,
    Dashboard,
    KnowledgeDocument,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeTextRequest,
    LLMSmokeResponse,
    MealItem,
    MealItemCreate,
    MealItemUpdate,
    MemberProfile,
    PlanningRequest,
    PlanningResponse,
    ShoppingItem,
    ShoppingItemCreate,
    ShoppingItemUpdate,
    WeeklyPlanDetail,
    WeeklyPlanSummary,
)
from app.schemas.domain import (
    AgentEvaluation,
    AgentStatus,
    ArchivedPlanResponse,
    BackgroundJobResponse,
    BackgroundKnowledgeJobCreate,
    BudgetAnalytics,
    BudgetSummary,
    CeleryStatsResponse,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionSummary,
    ChatSessionUpdate,
    ChatTurnResponse,
    DeadLetterItem,
    Expense,
    ExpenseCreate,
    ExpenseHistoryItem,
    ExpenseHistoryResponse,
    FeedbackEntry,
    FeedbackOverviewResponse,
    FeedbackSyncInfo,
    MealReplacementRequest,
    MealReplacementResponse,
    NutritionGoalResponse,
    NutritionReport,
    PromptRegistryResponse,
    PromptVersionInfo,
    QueueStats,
    RagEvalResponse,
    Recipe,
    RecipeInput,
    RecipeUpdate,
    ShoppingMergeResponse,
    SyncConsistencyResponse,
    TasteProfileResponse,
    UserProfileResponse,
    UserProfileUpdate,
)
from app.services.conversation import conversation_service
from app.services.documents import DocumentParseError
from app.services.domain import domain_operations_service
from app.services.feedback_loop import (
    EXPENSE_RECORD,
    SHOPPING_VERIFICATION,
    FeedbackSignal,
    FeedbackSyncResult,
    extract_taste_tags,
    feedback_loop_service,
)
from app.services.knowledge import get_knowledge_service
from app.services.nutrition import (
    build_nutrition_report,
    compute_nutrition_goal,
    nutrition_goal_to_targets,
)
from app.services.planning import planning_service
from app.services.runtime import runtime_state
from app.worker import celery_app

router = APIRouter()
settings = get_settings()
knowledge_service = get_knowledge_service()


async def _invalidate_user_cache(user_id: int) -> None:
    await runtime_state.delete_prefix(f"dashboard:{user_id}:")


@router.get("/health")
async def health() -> dict[str, str]:
    mode = settings.llm_provider if settings.real_llm_enabled else "demo"
    return {"status": "ok", "service": "solochef-api", "mode": mode}


@router.get("/ai/status", response_model=AIServiceStatus)
async def ai_status(context: CurrentContext) -> AIServiceStatus:
    result = await knowledge_service.status(context.user_id)
    return result.model_copy(update={"redis": await runtime_state.status()})


@router.post("/ai/llm/smoke", response_model=LLMSmokeResponse)
async def llm_smoke(_: OwnerContext) -> LLMSmokeResponse:
    try:
        return await smoke_test_llm(settings)
    except LLMGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/dashboard", response_model=Dashboard)
async def dashboard(context: CurrentContext, session: SessionDep) -> Dashboard:
    cache_key = f"dashboard:{context.user_id}:{context.user_id}"
    cached = await runtime_state.get_json(cache_key)
    if cached is not None:
        return Dashboard.model_validate(cached)
    now = datetime.now(UTC)
    active_plan = await PlanningRepository(session).get_active_plan(context.user_id)
    greeting = _time_based_greeting(now.hour) + f"，{context.display_name}"
    date_label = _format_date_label(now.date())
    placeholder_meal = MealItem(
        id=0,
        day="",
        name="尚未规划本周菜单",
        ingredients=[],
        tags=["待规划"],
        duration=0,
        cost=0,
        reason="先在 AI 规划里生成周计划，解锁今晚推荐与采购清单。",
    )
    # Phase 3 清理：calendar_events / plan_tasks / plan_budgets 表已删除，
    # 仪表盘的日程、任务改为空值，预算由活跃计划的 budget 列派生（即本周限额）。
    empty_budget = BudgetSummary(estimated=0, limit=0, saved=0, usage_percent=0, categories={})
    if active_plan is not None:
        plan_meals = [_meal_response(item) for item in active_plan.meals]
        tonight = plan_meals[0] if plan_meals else placeholder_meal
        plan_budget = BudgetSummary(
            estimated=0,
            limit=active_plan.budget,
            saved=active_plan.budget,
            usage_percent=0,
            categories={},
        )
        notices = _build_real_notices(plan_budget)
        response = Dashboard(
            user_name=context.display_name,
            greeting=greeting,
            date_label=date_label,
            today_events=[],
            tasks=[],
            tonight_meal=tonight,
            budget=plan_budget,
            notices=notices,
            week_progress=0,
        )
    else:
        notices = _build_real_notices(empty_budget, has_plan=False)
        response = Dashboard(
            user_name=context.display_name,
            greeting=greeting,
            date_label=date_label,
            today_events=[],
            tasks=[],
            tonight_meal=placeholder_meal,
            budget=empty_budget,
            notices=notices,
            week_progress=0,
        )
    await runtime_state.set_json(cache_key, response.model_dump(mode="json"), ttl=30)
    return response


_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _time_based_greeting(hour: int) -> str:
    if 5 <= hour < 11:
        return "早上好"
    if 11 <= hour < 13:
        return "中午好"
    if 13 <= hour < 18:
        return "下午好"
    if 18 <= hour < 23:
        return "晚上好"
    return "夜深了，注意休息"


def _format_date_label(today: date) -> str:
    return f"{today.month} 月 {today.day} 日 · {_WEEKDAY_CN[today.weekday()]}"


def _build_real_notices(
    budget: BudgetSummary,
    *,
    has_plan: bool = True,
) -> list[str]:
    # Phase 3 清理：calendar_events / plan_tasks 表删除后，过期日程与任务提示不再产生，
    # 仅保留预算使用率相关的提示。
    notices: list[str] = []
    if not has_plan:
        notices.append("尚未生成本周计划，前往 AI 规划一键生成")
        return notices
    if budget.usage_percent >= 90:
        notices.append(f"本周预算已使用 {budget.usage_percent}%，注意控制采购")
    elif budget.saved and budget.saved > 0:
        notices.append(f"本周预算还有 ¥{budget.saved} 结余，节奏良好")
    if not notices:
        notices.append("本周计划运转良好，继续保持")
    return notices


async def _graph_domain_context(
    user_id: int, session: SessionDep
) -> dict[str, list[dict[str, Any]]]:
    domain = DomainRepository(session)
    planning = PlanningRepository(session)
    recipes = await domain.list_recipes(user_id)
    plans = await planning.list_plans(user_id)
    # Phase 3 清理：plan_tasks / plan_budgets 表已删除，图谱上下文只保留菜谱与计划。
    return {
        "recipes": [
            {
                "id": item.id,
                "name": item.name,
                "tags": item.tags,
                "allergens": item.allergens,
                "ingredients": item.ingredients,
            }
            for item in recipes
        ],
        "plans": [
            {
                "id": item.id,
                "version": item.version,
                "summary": item.summary,
                "is_active": item.is_active,
            }
            for item in plans
        ],
    }


def _meal_response(meal: object) -> MealItem:
    return MealItem.model_validate(meal, from_attributes=True)


def _shopping_response(item: object) -> ShoppingItem:
    return ShoppingItem.model_validate(item, from_attributes=True)


def _recipe_response(recipe: RecipeRecord) -> Recipe:
    return Recipe.model_validate(recipe, from_attributes=True)


def _feedback_sync_info(result: FeedbackSyncResult | None) -> FeedbackSyncInfo | None:
    """把闭环回流结果转成接口视图，让前端能显示"已同步到图谱/知识库"。"""
    if result is None:
        return None
    return FeedbackSyncInfo(
        feedback_id=result.feedback_id,
        sentiment=result.sentiment,  # type: ignore[arg-type]
        deviation=result.deviation,
        graph_synced=result.graph_synced,
        vector_synced=result.vector_synced,
        notes=list(result.notes),
    )


def _price_sentiment(planned: float, actual: float) -> str:
    """核销价格偏差的确定性判定：显著超支为负向，明显省钱为正向。"""
    if planned <= 0:
        return "neutral"
    ratio = (actual - planned) / planned
    if ratio > 0.1:
        return "negative"
    if ratio < -0.1:
        return "positive"
    return "neutral"


async def _taste_profile_response(session: SessionDep, user_id: int) -> TasteProfileResponse:
    profile = await FeedbackRepository(session).taste_profile(user_id)
    return TasteProfileResponse(
        liked_tags=profile.liked_tags,
        disliked_tags=profile.disliked_tags,
        liked_dishes=profile.liked_dishes,
        rejected_dishes=profile.rejected_dishes,
        recent_notes=profile.recent_notes,
        sample_size=profile.sample_size,
    )


def _chat_summary(chat: object) -> ChatSessionSummary:
    return ChatSessionSummary.model_validate(chat, from_attributes=True)


def _chat_message(message: object) -> ChatMessageResponse:
    return ChatMessageResponse.model_validate(message, from_attributes=True)


def _job_response(job: object) -> BackgroundJobResponse:
    return BackgroundJobResponse.model_validate(job, from_attributes=True)


def _plan_summary_response(plan: WeeklyPlan) -> WeeklyPlanSummary:
    return WeeklyPlanSummary(
        id=plan.id,
        status=plan.status,
        version=plan.version,
        is_active=plan.is_active,
        parent_plan_id=plan.parent_plan_id,
        prompt=plan.prompt,
        budget=plan.budget,
        summary=plan.summary,
        created_at=plan.created_at,
        meal_count=len(plan.meals),
        task_count=0,
        shopping_count=len(plan.shopping_items),
    )


def _plan_detail_response(plan: WeeklyPlan) -> WeeklyPlanDetail:
    return WeeklyPlanDetail(
        id=plan.id,
        status=plan.status,
        version=plan.version,
        is_active=plan.is_active,
        parent_plan_id=plan.parent_plan_id,
        prompt=plan.prompt,
        budget=plan.budget,
        summary=plan.summary,
        conflicts=plan.conflicts,
        suggestions=plan.suggestions,
        run_id=plan.run_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        meals=[_meal_response(meal) for meal in plan.meals],
        shopping=[_shopping_response(item) for item in plan.shopping_items],
        tasks=[],
        budget_record=None,
    )


@router.get("/meals", response_model=list[MealItem])
async def meals(context: CurrentContext, session: SessionDep) -> list[MealItem]:
    plan = await PlanningRepository(session).get_active_plan(context.user_id)
    return [_meal_response(item) for item in plan.meals] if plan is not None else []


@router.post("/meals", response_model=MealItem, status_code=status.HTTP_201_CREATED)
async def create_meal(
    request: MealItemCreate, context: CurrentContext, session: SessionDep
) -> MealItem:
    item = await PlanningRepository(session).create_meal(
        context.user_id,
        **request.model_dump(),
    )
    return _meal_response(item)


@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(context: CurrentContext, session: SessionDep) -> UserProfileResponse:
    """获取用户画像，不存在时自动创建默认画像。"""
    profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == context.user_id)
    )
    if profile is None:
        profile = UserProfile(user_id=context.user_id)
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return UserProfileResponse.model_validate(profile, from_attributes=True)


@router.put("/profile", response_model=UserProfileResponse)
async def update_profile(
    request: UserProfileUpdate, context: CurrentContext, session: SessionDep
) -> UserProfileResponse:
    """更新用户画像，不存在时自动创建后更新。"""
    profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == context.user_id)
    )
    if profile is None:
        profile = UserProfile(user_id=context.user_id)
        session.add(profile)
    values = request.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(profile, key, value)
    await session.commit()
    await session.refresh(profile)
    await _invalidate_user_cache(context.user_id)
    return UserProfileResponse.model_validate(profile, from_attributes=True)


@router.get("/profile/nutrition-goal", response_model=NutritionGoalResponse)
async def get_nutrition_goal(
    context: CurrentContext, session: SessionDep
) -> NutritionGoalResponse:
    """获取当前营养目标快照，不存在时返回 404。"""
    goal = await session.scalar(
        select(NutritionGoal).where(NutritionGoal.user_id == context.user_id)
    )
    if goal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="尚未计算营养目标，请先 POST /profile/nutrition-goal",
        )
    return NutritionGoalResponse.model_validate(goal, from_attributes=True)


@router.post(
    "/profile/nutrition-goal",
    response_model=NutritionGoalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def compute_nutrition_goal_endpoint(
    context: CurrentContext, session: SessionDep
) -> NutritionGoalResponse:
    """根据用户画像按 Mifflin-St Jeor 公式计算营养目标并持久化。

    若已存在营养目标则覆盖更新（``user_id`` 唯一约束）。
    """
    profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == context.user_id)
    )
    if profile is None:
        profile = UserProfile(user_id=context.user_id)
        session.add(profile)
        await session.flush()
    goal = await session.scalar(
        select(NutritionGoal).where(NutritionGoal.user_id == context.user_id)
    )
    computed = compute_nutrition_goal(profile)
    if goal is None:
        goal = computed
        session.add(goal)
    else:
        for field in (
            "goal_type", "bmr", "tdee", "target_calories",
            "protein_g", "carb_g", "fat_g", "activity_level",
        ):
            setattr(goal, field, getattr(computed, field))
    await session.commit()
    await session.refresh(goal)
    await _invalidate_user_cache(context.user_id)
    return NutritionGoalResponse.model_validate(goal, from_attributes=True)


@router.get("/meals/nutrition", response_model=NutritionReport)
async def meal_nutrition(
    context: CurrentContext, session: SessionDep
) -> NutritionReport:
    """基于用户营养目标与活跃计划餐食，求解达成报告。"""
    repository = DomainRepository(session)
    planning = PlanningRepository(session)
    plan = await planning.get_active_plan(context.user_id)
    goal = await session.scalar(
        select(NutritionGoal).where(NutritionGoal.user_id == context.user_id)
    )
    targets = nutrition_goal_to_targets(goal) if goal is not None else None
    recipes = await repository.list_recipes(context.user_id)
    meals = list(plan.meals) if plan is not None else []
    return build_nutrition_report(meals, recipes, targets)


@router.get("/meals/taste-profile", response_model=TasteProfileResponse)
async def meal_taste_profile(
    context: CurrentContext, session: SessionDep
) -> TasteProfileResponse:
    """返回从历史反馈学到的口味画像，供前端展示“系统记住了什么”。

    必须声明在 ``/meals/{meal_id}`` 之前，否则会被路径参数路由抢先匹配。
    """
    return await _taste_profile_response(session, context.user_id)


@router.get("/meals/{meal_id}", response_model=MealItem)
async def get_meal(meal_id: int, context: CurrentContext, session: SessionDep) -> MealItem:
    item = await PlanningRepository(session).get_meal_item(meal_id, context.user_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="餐食记录不存在")
    return _meal_response(item)


@router.patch("/meals/{meal_id}", response_model=MealItem)
async def update_meal(
    meal_id: int,
    request: MealItemUpdate,
    context: CurrentContext,
    session: SessionDep,
) -> MealItem:
    repository = PlanningRepository(session)
    item = await repository.get_meal_item(meal_id, context.user_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="餐食记录不存在")
    for key, value in request.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await repository.save_item(item)
    return _meal_response(item)


@router.delete("/meals/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(meal_id: int, context: CurrentContext, session: SessionDep) -> None:
    repository = PlanningRepository(session)
    item = await repository.get_meal_item(meal_id, context.user_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="餐食记录不存在")
    await repository.delete_item(item)


@router.post("/meals/{meal_id}/replace", response_model=MealReplacementResponse)
async def replace_meal(
    meal_id: int,
    request: MealReplacementRequest,
    context: CurrentContext,
    session: SessionDep,
) -> MealReplacementResponse:
    item, sync = await domain_operations_service.replace_meal(
        session,
        user_id=context.user_id,
        meal_id=meal_id,
        feedback=request.feedback,
        rating=request.rating,
        tags=request.tags,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="餐食记录不存在")
    await _invalidate_user_cache(context.user_id)
    return MealReplacementResponse(
        meal=_meal_response(item),
        feedback=_feedback_sync_info(sync),
        taste_profile=await _taste_profile_response(session, context.user_id),
    )


@router.get("/shopping", response_model=list[ShoppingItem])
async def shopping(context: CurrentContext, session: SessionDep) -> list[ShoppingItem]:
    plan = await PlanningRepository(session).get_active_plan(context.user_id)
    return [_shopping_response(item) for item in plan.shopping_items] if plan is not None else []


@router.post("/shopping", response_model=ShoppingItem, status_code=status.HTTP_201_CREATED)
async def create_shopping_item(
    request: ShoppingItemCreate, context: CurrentContext, session: SessionDep
) -> ShoppingItem:
    item = await PlanningRepository(session).create_shopping_item(
        context.user_id,
        **request.model_dump(),
    )
    return _shopping_response(item)


@router.get("/shopping/{item_id}", response_model=ShoppingItem)
async def get_shopping_item(
    item_id: int, context: CurrentContext, session: SessionDep
) -> ShoppingItem:
    item = await PlanningRepository(session).get_shopping_item(item_id, context.user_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="购物条目不存在")
    return _shopping_response(item)


@router.patch("/shopping/{item_id}", response_model=ShoppingItem)
async def update_shopping_item(
    item_id: int,
    request: ShoppingItemUpdate,
    context: CurrentContext,
    session: SessionDep,
) -> ShoppingItem:
    repository = PlanningRepository(session)
    item = await repository.get_shopping_item(item_id, context.user_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="购物条目不存在")
    was_purchased = item.purchased
    planned_price = item.price
    for key, value in request.item_changes().items():
        setattr(item, key, value)
    await repository.save_item(item)
    # 采购项从未购买切换为已购买时，把核销结果回流为执行反馈
    # Phase 3 清理：库存入库（restock_from_shopping）随 inventory_items 表删除而移除
    if not was_purchased and item.purchased:
        actual_price = (
            request.actual_price if request.actual_price is not None else item.price
        )
        note = request.verification_note or ""
        await feedback_loop_service.capture(
            session,
            FeedbackSignal(
                user_id=context.user_id,
                feedback_type=SHOPPING_VERIFICATION,
                subject=item.name,
                content=note,
                reference_type="plan_shopping_item",
                reference_id=item.id,
                tags=extract_taste_tags(note, [item.category]),
                planned_value=float(planned_price),
                actual_value=float(actual_price),
                # 实付高于预估即视为负向偏差，供预算智能体下轮收紧
                sentiment=_price_sentiment(planned_price, actual_price) if not note else "",
                source="user" if note or request.actual_price is not None else "auto",
            ),
        )
        await _invalidate_user_cache(context.user_id)
    return _shopping_response(item)


@router.delete("/shopping/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shopping_item(item_id: int, context: CurrentContext, session: SessionDep) -> None:
    repository = PlanningRepository(session)
    item = await repository.get_shopping_item(item_id, context.user_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="购物条目不存在")
    await repository.delete_item(item)


@router.post("/shopping/merge", response_model=ShoppingMergeResponse)
async def merge_shopping(context: CurrentContext, session: SessionDep) -> ShoppingMergeResponse:
    merged_groups, removed_items, items, conversion_notes = (
        await DomainRepository(session).merge_shopping(context.user_id)
    )
    return ShoppingMergeResponse(
        merged_groups=merged_groups,
        removed_items=removed_items,
        items=[_shopping_response(item) for item in items],
        conversion_notes=conversion_notes,
    )


@router.get("/shopping/{item_id}/substitutions")
async def shopping_substitutions(
    item_id: int, context: CurrentContext, session: SessionDep
) -> dict[str, Any]:
    item = await PlanningRepository(session).get_shopping_item(item_id, context.user_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="购物项不存在")
    substitutions = {
        "牛肉": ["鸡胸肉", "豆腐"],
        "牛奶": ["无糖豆浆", "低脂牛奶"],
        "白米": ["糙米", "燕麦米"],
        "花生油": ["菜籽油", "橄榄油"],
    }
    matches = next((values for key, values in substitutions.items() if key in item.name), [])
    return {"item_id": item.id, "name": item.name, "suggestions": matches}


@router.get("/budget/expenses", response_model=list[Expense])
async def list_expenses(context: CurrentContext, session: SessionDep) -> list[Expense]:
    records = await DomainRepository(session).list_expenses(context.user_id)
    return [Expense.model_validate(record, from_attributes=True) for record in records]


@router.post("/budget/expenses", response_model=Expense, status_code=status.HTTP_201_CREATED)
async def create_expense(
    request: ExpenseCreate, context: CurrentContext, session: SessionDep
) -> Expense:
    try:
        record = await DomainRepository(session).create_expense(
            context.user_id,
            **request.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    # 闭环：实际支出与预算限额的偏差回流，让下一轮预算智能体看得到真实花销
    analytics = await domain_operations_service.budget_analytics(session, context.user_id)
    await feedback_loop_service.capture(
        session,
        FeedbackSignal(
            user_id=context.user_id,
            feedback_type=EXPENSE_RECORD,
            subject=record.category,
            content=record.note,
            reference_type="expense_record",
            reference_id=record.id,
            tags=(record.category,),
            planned_value=analytics.limit,
            actual_value=analytics.actual_spent,
            sentiment="negative" if analytics.warning else "neutral",
            source="user",
        ),
    )
    await _invalidate_user_cache(context.user_id)
    return Expense.model_validate(record, from_attributes=True)


@router.delete("/budget/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense_id: int, context: CurrentContext, session: SessionDep) -> None:
    deleted = await DomainRepository(session).delete_expense(expense_id, context.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="支出记录不存在")


@router.get("/budget/analytics", response_model=BudgetAnalytics)
async def budget_analytics(context: CurrentContext, session: SessionDep) -> BudgetAnalytics:
    return await domain_operations_service.budget_analytics(session, context.user_id)


@router.get("/feedback", response_model=FeedbackOverviewResponse)
async def feedback_overview(
    context: CurrentContext,
    session: SessionDep,
    feedback_type: Annotated[str, Query(max_length=40)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> FeedbackOverviewResponse:
    """执行反馈闭环总览：偏差明细、情感分布、待补偿同步数与口味画像。"""
    repository = FeedbackRepository(session)
    records = await repository.list_recent(
        context.user_id,
        feedback_types=(feedback_type,) if feedback_type else (),
        limit=limit,
    )
    pending = await repository.pending_sync(context.user_id)
    return FeedbackOverviewResponse(
        items=[FeedbackEntry.model_validate(row, from_attributes=True) for row in records],
        sentiment_counts=await repository.count_by_sentiment(context.user_id),
        pending_sync=len(pending),
        taste_profile=await _taste_profile_response(session, context.user_id),
    )


@router.post("/feedback/resync", response_model=FeedbackOverviewResponse)
async def resync_feedback(
    context: CurrentContext, session: SessionDep
) -> FeedbackOverviewResponse:
    """补偿重放：把此前因 Neo4j / Chroma 不可用而未回流的反馈再推一次。"""
    repository = FeedbackRepository(session)
    for row in await repository.pending_sync(context.user_id):
        await feedback_loop_service.replay(session, row)
    return await feedback_overview(context, session)


@router.get("/budget/expenses/history", response_model=ExpenseHistoryResponse)
async def expense_history(
    context: CurrentContext,
    session: SessionDep,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    category: str | None = None,
) -> ExpenseHistoryResponse:
    """按日期区间和分类查询家庭采购历史，附带分类汇总。"""
    repository = DomainRepository(session)
    records = await repository.list_expenses_filtered(
        context.user_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
    )
    categories = await repository.list_expense_categories(context.user_id)
    by_category: dict[str, float] = defaultdict(float)
    for record in records:
        by_category[record.category] += record.amount
    return ExpenseHistoryResponse(
        items=[
            ExpenseHistoryItem.model_validate(record, from_attributes=True)
            for record in records
        ],
        total_amount=round(sum(record.amount for record in records), 2),
        count=len(records),
        by_category={key: round(value, 2) for key, value in by_category.items()},
        categories=categories,
    )


@router.get("/recipes", response_model=list[Recipe])
async def list_recipes(context: CurrentContext, session: SessionDep) -> list[Recipe]:
    records = await DomainRepository(session).list_recipes(context.user_id)
    return [_recipe_response(record) for record in records]


@router.post("/recipes", response_model=Recipe, status_code=status.HTTP_201_CREATED)
async def create_recipe(
    request: RecipeInput, context: CurrentContext, session: SessionDep
) -> Recipe:
    try:
        recipe = await DomainRepository(session).create_recipe(
            context.user_id,
            **request.model_dump(),
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="菜谱名称已存在") from exc
    return _recipe_response(recipe)


@router.patch("/recipes/{recipe_id}", response_model=Recipe)
async def update_recipe(
    recipe_id: int,
    request: RecipeUpdate,
    context: CurrentContext,
    session: SessionDep,
) -> Recipe:
    repository = DomainRepository(session)
    recipe = await repository.get_recipe(recipe_id, context.user_id)
    if recipe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="菜谱不存在")
    return _recipe_response(
        await repository.save_recipe(recipe, **request.model_dump(exclude_unset=True))
    )


@router.delete("/recipes/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(recipe_id: int, context: CurrentContext, session: SessionDep) -> None:
    repository = DomainRepository(session)
    recipe = await repository.get_recipe(recipe_id, context.user_id)
    if recipe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="菜谱不存在")
    await repository.delete_recipe(recipe)


@router.get("/knowledge", response_model=list[KnowledgeDocument])
async def knowledge(context: CurrentContext) -> list[KnowledgeDocument]:
    try:
        return await knowledge_service.list_documents(context.user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Chroma 知识库不可用: {type(exc).__name__}",
        ) from exc


@router.post(
    "/knowledge/documents/text",
    response_model=KnowledgeDocument,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_knowledge_text(
    request: KnowledgeTextRequest, context: OwnerContext
) -> KnowledgeDocument:
    try:
        return await knowledge_service.ingest_text(
            name=request.name,
            category=request.category,
            content=request.content,
            user_id=context.user_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"知识入库失败: {type(exc).__name__}",
        ) from exc


@router.post(
    "/knowledge/documents/upload",
    response_model=KnowledgeDocument,
    status_code=status.HTTP_201_CREATED,
)
async def upload_knowledge_document(
    context: OwnerContext,
    file: UploadFile = File(...),  # noqa: B008
    category: str = Form(default="家庭知识"),  # noqa: B008
) -> KnowledgeDocument:
    payload = await file.read()
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 10 MB")
    try:
        return await knowledge_service.ingest_file(
            name=file.filename or "knowledge.txt",
            category=category,
            payload=payload,
            user_id=context.user_id,
        )
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"文档向量化失败: {type(exc).__name__}",
        ) from exc


@router.post("/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: KnowledgeSearchRequest, context: CurrentContext, session: SessionDep
) -> KnowledgeSearchResponse:
    profiles: list[MemberProfile] = []
    # Phase 3 清理：calendar_events 表已删除，日程上下文恒为空
    events: list = []
    domain = await _graph_domain_context(context.user_id, session)
    return await knowledge_service.search(
        request.query,
        context.user_id,
        request.top_k,
        members=profiles,
        events=events,
        domain=domain,
    )


@router.post("/knowledge/bootstrap", response_model=list[KnowledgeDocument])
async def bootstrap_knowledge(
    context: OwnerContext, session: SessionDep
) -> list[KnowledgeDocument]:
    try:
        profiles: list[MemberProfile] = []
        # Phase 3 清理：calendar_events 表已删除，日程上下文恒为空
        events: list = []
        domain = await _graph_domain_context(context.user_id, session)
        return await knowledge_service.bootstrap(context.user_id, profiles, events, domain)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"初始化知识库失败: {type(exc).__name__}",
        ) from exc


@router.delete("/knowledge/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_document(document_id: str, context: OwnerContext) -> None:
    await knowledge_service.vector_store.delete_document(document_id, context.user_id)


@router.post(
    "/knowledge/jobs/text",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_knowledge_text(
    request: BackgroundKnowledgeJobCreate,
    context: OwnerContext,
    session: SessionDep,
) -> BackgroundJobResponse:
    # 统一幂等键：命中缓存则直接返回既有结果，避免重复入库
    if request.idempotency_key:
        cached = await runtime_state.get_idempotent(request.idempotency_key)
        if cached is not None:
            return BackgroundJobResponse.model_validate(cached)
    repository = BackgroundJobRepository(session)
    job = await repository.create(
        user_id=context.user_id,
        kind="knowledge_text",
        payload=request.model_dump(),
        idempotency_key=request.idempotency_key,
        priority=request.priority,
    )
    try:
        celery_app.send_task(
            "solochef.process_knowledge_text",
            args=[job.id],
            task_id=job.id,
        )
    except Exception as exc:
        await repository.mark_failed(job, f"Celery enqueue failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="后台任务队列不可用",
        ) from exc
    response = _job_response(job)
    if request.idempotency_key:
        await runtime_state.set_idempotent(
            request.idempotency_key, response.model_dump(mode="json")
        )
    return response


@router.post(
    "/knowledge/jobs/upload",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_knowledge_upload(
    context: OwnerContext,
    session: SessionDep,
    file: UploadFile = File(...),  # noqa: B008
    category: str = Form(default="家庭知识"),  # noqa: B008
) -> BackgroundJobResponse:
    payload = await file.read()
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 10 MB")
    repository = BackgroundJobRepository(session)
    job = await repository.create(
        user_id=context.user_id,
        kind="knowledge_file",
        payload={
            "name": file.filename or "knowledge.txt",
            "category": category,
            "content_base64": b64encode(payload).decode("ascii"),
        },
    )
    try:
        celery_app.send_task("solochef.process_knowledge_file", args=[job.id], task_id=job.id)
    except Exception as exc:
        await repository.mark_failed(job, f"Celery enqueue failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="后台任务队列不可用",
        ) from exc
    return _job_response(job)


@router.post(
    "/knowledge/jobs/graph-sync",
    response_model=BackgroundJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_graph_sync(context: OwnerContext, session: SessionDep) -> BackgroundJobResponse:
    repository = BackgroundJobRepository(session)
    job = await repository.create(
        user_id=context.user_id,
        kind="graph_sync",
        payload={},
    )
    try:
        celery_app.send_task("solochef.sync_member_graph", args=[job.id], task_id=job.id)
    except Exception as exc:
        await repository.mark_failed(job, f"Celery enqueue failed: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="后台任务队列不可用",
        ) from exc
    return _job_response(job)


@router.get("/jobs/dead-letter", response_model=list[DeadLetterItem])
async def list_dead_letter(
    context: CurrentContext, session: SessionDep
) -> list[DeadLetterItem]:
    """返回家庭的死信任务列表（重试耗尽后转入）。"""
    jobs = await BackgroundJobRepository(session).list_dead_letter(context.user_id)
    return [
        DeadLetterItem(
            id=job.id,
            kind=job.kind,
            error_message=job.error_message,
            priority=job.priority,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )
        for job in jobs
    ]


@router.post("/jobs/cleanup", response_model=dict[str, int])
async def cleanup_jobs(
    context: OwnerContext,
    session: SessionDep,
    days_old: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, int]:
    """手动触发终态任务清理（定时任务每天凌晨 3 点自动执行）。"""
    cutoff = datetime.now(UTC) - timedelta(days=days_old)
    removed = await BackgroundJobRepository(session).prune_terminal_before(cutoff)
    return {"removed": removed}


@router.get("/admin/celery/stats", response_model=CeleryStatsResponse)
async def celery_stats(
    context: OwnerContext, session: SessionDep
) -> CeleryStatsResponse:
    """返回 Celery 运行时监控快照：队列深度、状态计数、最近任务与死信数。"""
    from app.worker import celery_app

    repository = BackgroundJobRepository(session)
    status_counts = await repository.count_by_status(context.user_id)
    recent = await repository.list_recent(context.user_id, limit=10)
    dead_letters = await repository.list_dead_letter(context.user_id)
    queue_names = ["default", "knowledge", "graph", "maintenance"]
    queues: list[QueueStats] = []
    broker_connected = await runtime_state.status() == "connected"
    for name in queue_names:
        depth = await runtime_state.get_queue_depth(name) if broker_connected else 0
        queues.append(QueueStats(name=name, depth=depth, routing_key=name))
    result_expires = int(getattr(celery_app.conf, "result_expires", 3600) or 3600)
    return CeleryStatsResponse(
        broker_connected=broker_connected,
        queues=queues,
        status_counts=status_counts,
        recent_jobs=[_job_response(job) for job in recent],
        dead_letter_count=len(dead_letters),
        result_expires=result_expires,
        active_queues=queue_names,
    )


@router.get("/admin/rag/sync", response_model=SyncConsistencyResponse)
async def rag_sync_consistency(
    context: OwnerContext, session: SessionDep
) -> SyncConsistencyResponse:
    """返回 Chroma 与 Neo4j 检索索引的同步一致性快照。"""
    del session
    return await knowledge_service.consistency_report(context.user_id)


@router.get("/admin/rag/eval", response_model=RagEvalResponse)
async def rag_evaluation(
    context: OwnerContext, session: SessionDep, top_k: int = 4
) -> RagEvalResponse:
    """对内置家庭场景评测集运行离线检索质量评测，返回 Recall@k / nDCG@k。"""
    del session
    from app.core.config import get_settings
    from app.services.rag_eval import evaluate_retrieval

    settings = get_settings()
    return await evaluate_retrieval(
        knowledge_service, context.user_id, settings, top_k=min(max(top_k, 1), 20)
    )


@router.get("/jobs/{job_id}", response_model=BackgroundJobResponse)
async def get_background_job(
    job_id: UUID, context: CurrentContext, session: SessionDep
) -> BackgroundJobResponse:
    job = await BackgroundJobRepository(session).get(str(job_id), context.user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="后台任务不存在")
    return _job_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=BackgroundJobResponse)
async def cancel_background_job(
    job_id: UUID, context: OwnerContext, session: SessionDep
) -> BackgroundJobResponse:
    """取消后台任务：撤销 Celery 任务并标记数据库状态。"""
    from app.worker import cancel_running_task

    repository = BackgroundJobRepository(session)
    job = await repository.get(str(job_id), context.user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="后台任务不存在")
    if job.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"任务已处于终态（{job.status}），无法取消",
        )
    cancel_running_task(str(job_id))
    await repository.cancel(job)
    return _job_response(job)


@router.post(
    "/chat/sessions",
    response_model=ChatSessionSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_session(
    request: ChatSessionCreate, context: CurrentContext, session: SessionDep
) -> ChatSessionSummary:
    chat = await ConversationRepository(session).create_session(
        context.user_id,
        request.title,
    )
    return _chat_summary(chat)


@router.get("/chat/sessions", response_model=list[ChatSessionSummary])
async def list_chat_sessions(
    context: CurrentContext,
    session: SessionDep,
    query: str = Query(default="", max_length=120),
) -> list[ChatSessionSummary]:
    records = await ConversationRepository(session).list_sessions(context.user_id, query)
    return [_chat_summary(record) for record in records]


@router.get("/chat/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(
    session_id: UUID, context: CurrentContext, session: SessionDep
) -> ChatSessionDetail:
    chat = await ConversationRepository(session).get_session(str(session_id), context.user_id)
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    summary = _chat_summary(chat)
    return ChatSessionDetail(
        **summary.model_dump(),
        messages=[_chat_message(message) for message in chat.messages],
    )


@router.patch("/chat/sessions/{session_id}", response_model=ChatSessionSummary)
async def rename_chat_session(
    session_id: UUID,
    request: ChatSessionUpdate,
    context: CurrentContext,
    session: SessionDep,
) -> ChatSessionSummary:
    repository = ConversationRepository(session)
    chat = await repository.get_session(str(session_id), context.user_id)
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    await repository.rename_session(chat, request.title)
    return _chat_summary(chat)


@router.delete("/chat/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat_session(
    session_id: UUID, context: CurrentContext, session: SessionDep
) -> None:
    repository = ConversationRepository(session)
    chat = await repository.get_session(str(session_id), context.user_id)
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    await repository.delete_session(chat)


@router.post("/chat/sessions/{session_id}/messages", response_model=ChatTurnResponse)
async def create_chat_turn(
    session_id: UUID,
    request: ChatMessageCreate,
    context: CurrentContext,
    session: SessionDep,
) -> ChatTurnResponse:
    if not await runtime_state.allow(
        f"chat:{context.user_id}:{context.user_id}", limit=20, window_seconds=60
    ):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求过于频繁")
    members: list[MemberProfile] = []
    # Phase 3 清理：calendar_events 表已删除，日程上下文恒为空
    events: list = []
    result = await conversation_service.run_turn(
        session,
        session_id=str(session_id),
        user_id=context.user_id,
        content=request.content,
        budget=request.budget,
        members=members,
        events=events,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    return result


@router.post("/chat/sessions/{session_id}/messages/stream")
async def stream_chat_turn(
    session_id: UUID,
    request: ChatMessageCreate,
    context: CurrentContext,
    session: SessionDep,
) -> StreamingResponse:
    repository = ConversationRepository(session)
    if await repository.get_session(str(session_id), context.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    if not await runtime_state.allow(
        f"chat:{context.user_id}:{context.user_id}", limit=20, window_seconds=60
    ):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求过于频繁")
    members: list[MemberProfile] = []
    # Phase 3 清理：calendar_events 表已删除，日程上下文恒为空
    events: list = []
    stream = conversation_service.stream_turn(
        session,
        session_id=str(session_id),
        user_id=context.user_id,
        content=request.content,
        budget=request.budget,
        members=members,
        events=events,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/sessions/{session_id}/cancel")
async def cancel_chat_turn(
    session_id: UUID, context: CurrentContext, session: SessionDep
) -> dict[str, str]:
    chat = await ConversationRepository(session).get_session(str(session_id), context.user_id)
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    await runtime_state.set_cancelled(str(session_id))
    return {"status": "cancelling"}


@router.get("/chat/sessions/{session_id}/events")
async def replay_chat_events(
    session_id: UUID,
    context: CurrentContext,
    session: SessionDep,
    after: str = "",
) -> dict[str, Any]:
    """重放指定会话的 SSE 事件，供前端断线重连补齐。"""
    chat = await ConversationRepository(session).get_session(
        str(session_id), context.user_id
    )
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="对话不存在")
    events = await runtime_state.get_events_since(str(session_id), after)
    turn_status = await runtime_state.get_turn_status(str(session_id))
    return {"events": events, "turn_status": turn_status}


@router.post(
    "/plans/generate-weekly", response_model=PlanningResponse, status_code=status.HTTP_201_CREATED
)
async def generate_weekly(
    request: PlanningRequest, context: CurrentContext, session: SessionDep
) -> PlanningResponse:
    if not await runtime_state.allow(
        f"plan:{context.user_id}:{context.user_id}", limit=30, window_seconds=60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="规划请求过于频繁",
        )
    scoped_request = request.model_copy(
        update={"user_id": context.user_id}
    )
    profiles: list[MemberProfile] = []
    # Phase 3 清理：calendar_events 表已删除，日程上下文恒为空
    events: list = []
    return await planning_service.generate(
        scoped_request, members=profiles, events=events, session=session
    )


@router.post("/plans/{run_id}/confirm")
async def confirm_plan(
    run_id: UUID, context: CurrentContext, session: SessionDep
) -> dict[str, Any]:
    repo = PlanningRepository(session)
    record = await repo.get_agent_run(str(run_id), context.user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    if record.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent run is not completed yet",
        )

    existing = await repo.get_plan_by_run_id(str(run_id), context.user_id)
    if existing is not None:
        return {
            "plan_id": existing.id,
            "run_id": str(run_id),
            "status": "confirmed",
            "message": "计划已存在（幂等确认）",
        }

    # Phase 3 清理：calendar_events 表删除后，确认计划不再做日程冲突校验
    payload = record.payload or {}
    budget_payload = payload.get("budget", {})
    try:
        plan = await repo.create_confirmed_plan(
            user_id=context.user_id,
            run_id=str(run_id),
            prompt=record.prompt,
            plan_values={
                "budget": budget_payload.get("limit", 500),
                "summary": payload.get("summary", ""),
                "conflicts": payload.get("conflicts", []),
                "suggestions": payload.get("suggestions", []),
            },
            meals=payload.get("meals", []),
            shopping=payload.get("shopping", []),
        )
    except IntegrityError:
        await session.rollback()
        existing_plan = await repo.get_plan_by_run_id(str(run_id), context.user_id)
        if existing_plan is None:
            raise
        plan = existing_plan
    await _invalidate_user_cache(context.user_id)
    return {
        "plan_id": plan.id,
        "run_id": str(run_id),
        "status": "confirmed",
        "message": "计划已保存到家庭空间",
    }


@router.get("/agents/prompts", response_model=PromptRegistryResponse)
async def agent_prompts(_: CurrentContext) -> PromptRegistryResponse:
    """返回全部领域智能体的提示词版本注册表。"""
    agents = {
        name: [
            PromptVersionInfo(
                name=version.name,
                version=version.version,
                system_message=version.system_message,
                instruction=version.instruction,
                changelog=version.changelog,
                released_at=version.released_at,
                is_active=(version is versions[-1]),
            )
            for version in versions
        ]
        for name, versions in list_versions().items()
    }
    active_versions = {name: get_active(name).version for name in agent_names()}
    return PromptRegistryResponse(agents=agents, active_versions=active_versions)


@router.get("/agents/evaluate", response_model=AgentEvaluation)
async def agent_evaluate(
    context: CurrentContext, session: SessionDep
) -> AgentEvaluation:
    """对活跃计划执行领域智能体评测，输出综合评分与逐项明细。

    忌口约束来自单人 ``UserProfile.constraints``，是 SoloChef 忌口校验的唯一
    数据源（替代了家庭时期的 ``MemberProfile``）。
    """
    planning = PlanningRepository(session)
    profile = await session.scalar(
        select(UserProfile).where(UserProfile.user_id == context.user_id)
    )
    constraints = list(profile.constraints) if profile is not None else []
    plan = await planning.get_active_plan(context.user_id)
    if plan is None:
        return evaluate_plan(
            meals=[],
            shopping=[],
            budget=None,
            constraints=constraints,
            plan_budget_limit=500.0,
        )
    meals = [_meal_response(item) for item in plan.meals]
    shopping = [_shopping_response(item) for item in plan.shopping_items]
    budget = BudgetSummary(
        estimated=0, limit=plan.budget, saved=plan.budget, usage_percent=0, categories={}
    )
    evaluation = evaluate_plan(
        meals=meals,
        shopping=shopping,
        budget=budget,
        constraints=constraints,
        plan_budget_limit=plan.budget,
    )
    evaluation.prompt_versions = {
        name: get_active(name).version for name in agent_names()
    }
    return evaluation


@router.get("/agents/runs/{run_id}", response_model=AgentRun)
async def agent_run(run_id: UUID, context: CurrentContext, session: SessionDep) -> AgentRun:
    run = await planning_service.get_run(run_id, context.user_id, session)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


@router.get("/agents/runs", response_model=list[AgentRun])
async def agent_runs(context: CurrentContext, session: SessionDep) -> list[AgentRun]:
    repo = PlanningRepository(session)
    records = await repo.list_agent_runs(context.user_id)
    return [
        AgentRun(
            id=UUID(record.id),
            request=record.prompt,
            status=AgentStatus(record.status),
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_ms=record.duration_ms,
            steps=[],
            error_message=record.error_message,
            error_type=record.error_type,
            failed_step=record.failed_step,
            checkpoint=record.checkpoint or {},
        )
        for record in records
    ]


@router.post("/agents/runs/{run_id}/retry", response_model=PlanningResponse)
async def retry_agent_run(
    run_id: UUID, context: CurrentContext, session: SessionDep
) -> PlanningResponse:
    record = await PlanningRepository(session).get_agent_run(str(run_id), context.user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run 不存在")
    if record.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有失败的 Agent Run 可以恢复",
        )
    resumed = await planning_service.resume(
        run_id,
        user_id=context.user_id,
        session=session,
    )
    if resumed is not None:
        return resumed
    members: list[MemberProfile] = []
    # Phase 3 清理：calendar_events 表已删除，日程上下文恒为空
    events: list = []
    budget_value = (record.payload or {}).get("budget", {})
    budget = float(budget_value.get("limit", 500)) if isinstance(budget_value, dict) else 500
    return await planning_service.generate(
        PlanningRequest(
            prompt=record.prompt,
            budget=budget,
            user_id=context.user_id,
        ),
        members=members,
        events=events,
        session=session,
    )


@router.get("/plans", response_model=list[WeeklyPlanSummary])
async def list_plans(context: CurrentContext, session: SessionDep) -> list[WeeklyPlanSummary]:
    repo = PlanningRepository(session)
    plans = await repo.list_plans(context.user_id)
    return [_plan_summary_response(plan) for plan in plans]


@router.get("/plans/{plan_id}/versions", response_model=list[WeeklyPlanSummary])
async def list_plan_versions(
    plan_id: int, context: CurrentContext, session: SessionDep
) -> list[WeeklyPlanSummary]:
    plans = await PlanningRepository(session).list_plan_versions(plan_id, context.user_id)
    if plans is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return [_plan_summary_response(plan) for plan in plans]


@router.get("/plans/{plan_id}/diff/{other_plan_id}")
async def compare_plans(
    plan_id: int, other_plan_id: int, context: CurrentContext, session: SessionDep
) -> dict[str, Any]:
    repository = PlanningRepository(session)
    left = await repository.get_plan(plan_id, context.user_id)
    right = await repository.get_plan(other_plan_id, context.user_id)
    if left is None or right is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="计划不存在")

    def values(item: object) -> dict[str, Any]:
        return {
            column: value
            for column, value in vars(item).items()
            if not column.startswith("_")
            and column not in {"id", "plan_id", "created_at", "updated_at"}
        }

    sections: dict[str, Any] = {}
    for name, left_items, right_items, key in (
        ("meals", left.meals, right.meals, "day"),
        ("shopping", left.shopping_items, right.shopping_items, "name"),
    ):
        before = {str(getattr(item, key)): values(item) for item in left_items}
        after = {str(getattr(item, key)): values(item) for item in right_items}
        sections[name] = {
            "added": [after[item] for item in after.keys() - before.keys()],
            "removed": [before[item] for item in before.keys() - after.keys()],
            "changed": [
                {"key": item, "before": before[item], "after": after[item]}
                for item in before.keys() & after.keys()
                if before[item] != after[item]
            ],
        }
    return {
        "from_version": left.version,
        "to_version": right.version,
        "sections": sections,
    }


@router.post("/plans/{plan_id}/derive", response_model=WeeklyPlanDetail)
async def derive_plan(
    plan_id: int, context: CurrentContext, session: SessionDep
) -> WeeklyPlanDetail:
    repository = PlanningRepository(session)
    source = await repository.get_plan(plan_id, context.user_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="计划不存在")
    derived = await repository.derive_plan(source, user_id=context.user_id)
    return _plan_detail_response(derived)


@router.post("/plans/{plan_id}/activate", response_model=WeeklyPlanDetail)
async def activate_plan(
    plan_id: int, context: CurrentContext, session: SessionDep
) -> WeeklyPlanDetail:
    plan = await PlanningRepository(session).activate_plan(plan_id, context.user_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return _plan_detail_response(plan)


@router.post("/plans/{plan_id}/rollback", response_model=WeeklyPlanDetail)
async def rollback_plan(
    plan_id: int, context: CurrentContext, session: SessionDep
) -> WeeklyPlanDetail:
    repository = PlanningRepository(session)
    current = await repository.get_plan(plan_id, context.user_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    if current.parent_plan_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plan has no previous version",
        )
    plan = await repository.rollback_plan(plan_id, context.user_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Previous plan version not found",
        )
    return _plan_detail_response(plan)


@router.post("/plans/{plan_id}/archive", response_model=ArchivedPlanResponse)
async def archive_plan(
    plan_id: int, context: CurrentContext, session: SessionDep
) -> ArchivedPlanResponse:
    """将计划独立归档（status=archived，取消激活），与版本回滚解耦。"""
    plan = await PlanningRepository(session).archive_plan(plan_id, context.user_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return ArchivedPlanResponse(
        id=plan.id,
        status=plan.status,
        is_active=plan.is_active,
        archived_at=datetime.now(UTC),
    )


@router.get("/plans/archived", response_model=list[WeeklyPlanSummary])
async def list_archived_plans(
    context: CurrentContext, session: SessionDep
) -> list[WeeklyPlanSummary]:
    plans = await PlanningRepository(session).list_archived_plans(context.user_id)
    return [_plan_summary_response(plan) for plan in plans]


@router.get("/plans/{plan_id}", response_model=WeeklyPlanDetail)
async def get_plan(plan_id: int, context: CurrentContext, session: SessionDep) -> WeeklyPlanDetail:
    repo = PlanningRepository(session)
    plan = await repo.get_plan(plan_id, context.user_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return _plan_detail_response(plan)
