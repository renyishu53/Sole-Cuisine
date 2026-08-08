from datetime import date

from app.schemas import CalendarEvent, KnowledgeDocument, ShoppingItem, TaskItem
from app.schemas.domain import BudgetSummary, Dashboard, MealItem, TaskStatus

EVENTS = [
    CalendarEvent(
        id=1, title="健身", member="本人", day="周三", time="19:00", category="运动"
    ),
    CalendarEvent(id=2, title="集中采购", member="本人", day="周六", time="10:00", category="日常"),
    CalendarEvent(
        id=3, title="一周备餐", member="本人", day="周日", time="17:00", category="计划"
    ),
    CalendarEvent(
        id=4,
        title="加班",
        member="本人",
        day="周三",
        time="18:00",
        category="工作",
        conflict=True,
    ),
]

TASKS = [
    TaskItem(
        id=1,
        title="整理冰箱库存",
        assignee="本人",
        duration=15,
        due="今天 19:00",
        status=TaskStatus.DOING,
        category="整理",
    ),
    TaskItem(
        id=2,
        title="厨房台面清洁",
        assignee="本人",
        duration=15,
        due="今天 21:00",
        status=TaskStatus.TODO,
        category="清洁",
    ),
    TaskItem(
        id=3,
        title="补充调味品",
        assignee="本人",
        duration=10,
        due="周六 10:00",
        status=TaskStatus.DONE,
        category="采购",
    ),
    TaskItem(
        id=4,
        title="备餐分装",
        assignee="本人",
        duration=20,
        due="周日 18:00",
        status=TaskStatus.TODO,
        category="备餐",
    ),
]

MEALS = [
    MealItem(
        day="周一",
        name="番茄鸡蛋面",
        duration=25,
        cost=18,
        tags=["不辣", "一人食"],
        reason="复用番茄和鸡蛋，准备简单",
        ingredients=["番茄", "鸡蛋", "面条"],
    ),
    MealItem(
        day="周二",
        name="鸡胸菌菇汤",
        duration=30,
        cost=24,
        tags=["少糖", "高蛋白"],
        reason="兼顾清淡口味和蛋白质",
        ingredients=["鸡胸肉", "香菇", "青菜"],
    ),
    MealItem(
        day="周三",
        name="虾仁滑蛋盖饭",
        duration=18,
        cost=28,
        tags=["快手", "不辣"],
        reason="避开加班冲突，18 分钟出餐",
        ingredients=["虾仁", "鸡蛋", "米饭"],
    ),
    MealItem(
        day="周四",
        name="清蒸鲈鱼",
        duration=35,
        cost=38,
        tags=["清淡", "少糖"],
        reason="少油调味，适合独居轻食",
        ingredients=["鲈鱼", "生姜", "青菜"],
    ),
    MealItem(
        day="周五",
        name="菌菇豆腐煲",
        duration=28,
        cost=22,
        tags=["轻食", "复用"],
        reason="复用周二菌菇，减少浪费",
        ingredients=["豆腐", "香菇", "青菜"],
    ),
    MealItem(
        day="周六",
        name="鸡腿时蔬饭",
        duration=40,
        cost=32,
        tags=["周末", "高蛋白"],
        reason="采购日使用新鲜时蔬",
        ingredients=["鸡腿", "西兰花", "胡萝卜"],
    ),
    MealItem(
        day="周日",
        name="冬瓜丸子汤",
        duration=32,
        cost=25,
        tags=["清淡", "备餐"],
        reason="批量备餐，为下周留出轻松晚餐",
        ingredients=["冬瓜", "肉丸", "香葱"],
    ),
]

SHOPPING = [
    ShoppingItem(
        id=1,
        name="鸡蛋",
        category="肉蛋奶",
        quantity="6 个",
        price=8,
        source="周一 / 周三",
        purchased=True,
    ),
    ShoppingItem(id=2, name="番茄", category="蔬菜", quantity="3 个", price=6, source="周一"),
    ShoppingItem(id=3, name="虾仁", category="肉蛋奶", quantity="150g", price=22, source="周三"),
    ShoppingItem(id=4, name="鲈鱼", category="肉蛋奶", quantity="1 条", price=32, source="周四"),
    ShoppingItem(
        id=5,
        name="西兰花",
        category="蔬菜",
        quantity="1 颗",
        price=8,
        source="周六",
        purchased=True,
    ),
    ShoppingItem(
        id=6, name="香菇", category="蔬菜", quantity="250g", price=10, source="周二 / 周五"
    ),
    ShoppingItem(id=7, name="豆腐", category="肉蛋奶", quantity="1 盒", price=5, source="周五"),
    ShoppingItem(id=8, name="面条", category="主食", quantity="1 袋", price=6, source="周一"),
]

DOCUMENTS = [
    KnowledgeDocument(
        id=1,
        name="独居快手晚餐菜谱.md",
        category="菜谱",
        status="ready",
        chunks=128,
        updated_at=date(2026, 7, 28),
    ),
    KnowledgeDocument(
        id=2,
        name="厨房清洁指南.pdf",
        category="清洁",
        status="processing",
        chunks=36,
        updated_at=date(2026, 7, 30),
    ),
    KnowledgeDocument(
        id=3,
        name="控糖饮食常识.md",
        category="营养",
        status="ready",
        chunks=64,
        updated_at=date(2026, 7, 26),
    ),
]

BUDGET = BudgetSummary(
    limit=300,
    estimated=268,
    saved=32,
    usage_percent=89,
    categories={"肉蛋奶": 108, "蔬菜": 64, "主食": 38, "调味品": 24, "日用品": 34},
)


def get_dashboard() -> Dashboard:
    return Dashboard(
        user_name="本人",
        greeting="晚上好",
        date_label="7 月 31 日 · 周五",
        today_events=EVENTS[:2],
        tasks=TASKS,
        tonight_meal=MEALS[4],
        budget=BUDGET,
        notices=["周三 18:00 存在日程冲突", "本周采购预计低于预算 32 元"],
        week_progress=68,
    )
