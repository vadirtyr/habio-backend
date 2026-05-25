from datetime import datetime, timezone, timedelta


QUEST_DEFS = [
    {
        "id": "daily-3-habits",
        "name": "Habit Hat-Trick",
        "description": "Complete 3 habits today",
        "icon": "Flame",
        "period": "daily",
        "target": 3,
        "metric": "habits_today",
        "reward": 25,
    },
    {
        "id": "daily-task",
        "name": "Get One Done",
        "description": "Complete any task today",
        "icon": "Check",
        "period": "daily",
        "target": 1,
        "metric": "tasks_today",
        "reward": 15,
    },
    {
        "id": "weekly-10-habits",
        "name": "Habit Streaker",
        "description": "Complete 10 habits this week",
        "icon": "Zap",
        "period": "weekly",
        "target": 10,
        "metric": "habits_this_week",
        "reward": 50,
    },
    {
        "id": "weekly-3-tasks",
        "name": "Task Master",
        "description": "Complete 3 tasks this week",
        "icon": "Award",
        "period": "weekly",
        "target": 3,
        "metric": "tasks_this_week",
        "reward": 40,
    },
]


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def get_period_key(period: str) -> str:
    today_dt = datetime.now(timezone.utc).date()

    if period == "daily":
        return today_dt.isoformat()

    if period == "weekly":
        iso = today_dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    return ""


def week_start_iso() -> str:
    today_dt = datetime.now(timezone.utc).date()
    monday = today_dt - timedelta(days=today_dt.weekday())
    return monday.isoformat()


async def compute_quest_metrics(db, uid: str) -> dict:
    today = today_str()
    monday = week_start_iso()

    habits = await db.habits.find(
        {"user_id": uid},
        {"_id": 0, "completions": 1},
    ).to_list(1000)

    habits_today = sum(
        1 for h in habits if today in h.get("completions", [])
    )

    habits_this_week = sum(
        1
        for h in habits
        for d in h.get("completions", [])
        if d >= monday
    )

    tasks_today = await db.tasks.count_documents({
        "user_id": uid,
        "completed": True,
        "completed_at": {"$gte": today + "T00:00:00+00:00"},
    })

    tasks_this_week = await db.tasks.count_documents({
        "user_id": uid,
        "completed": True,
        "completed_at": {"$gte": monday + "T00:00:00+00:00"},
    })

    return {
        "habits_today": habits_today,
        "habits_this_week": habits_this_week,
        "tasks_today": tasks_today,
        "tasks_this_week": tasks_this_week,
    }