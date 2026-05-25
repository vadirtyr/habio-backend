from datetime import datetime, timezone


ACHIEVEMENT_DEFS = [
    {"id": "first-habit", "category": "Habits", "name": "First Steps", "description": "Create your first habit", "icon": "Flame", "color": "#EF476F", "target": 1, "metric": "habits_count"},
    {"id": "habits-25", "category": "Habits", "name": "Habit Builder", "description": "Complete habits 25 total times", "icon": "Flame", "color": "#22C55E", "target": 25, "metric": "total_habit_completions"},

    {"id": "first-task", "category": "Tasks", "name": "On a Mission", "description": "Create your first task", "icon": "ListChecks", "color": "#118AB2", "target": 1, "metric": "tasks_total"},
    {"id": "tasks-10", "category": "Tasks", "name": "Task Slayer", "description": "Complete 10 tasks", "icon": "Award", "color": "#06D6A0", "target": 10, "metric": "tasks_done"},
    {"id": "tasks-50", "category": "Tasks", "name": "Productivity Pro", "description": "Complete 50 tasks", "icon": "Trophy", "color": "#FFD166", "target": 50, "metric": "tasks_done"},

    {"id": "streak-3", "category": "Streaks", "name": "Warming Up", "description": "Reach a 3-day streak", "icon": "Flame", "color": "#FFD166", "target": 3, "metric": "best_streak"},
    {"id": "streak-7", "category": "Streaks", "name": "On Fire", "description": "Reach a 7-day streak", "icon": "Flame", "color": "#EF476F", "target": 7, "metric": "best_streak"},
    {"id": "streak-30", "category": "Streaks", "name": "Unstoppable", "description": "Reach a 30-day streak", "icon": "Zap", "color": "#EF476F", "target": 30, "metric": "best_streak"},

    {"id": "coins-100", "category": "Coins", "name": "Pocket Change", "description": "Earn 100 coins", "icon": "Coins", "color": "#FFD166", "target": 100, "metric": "total_earned"},
    {"id": "coins-500", "category": "Coins", "name": "Coin Collector", "description": "Earn 500 coins", "icon": "Coins", "color": "#FFD166", "target": 500, "metric": "total_earned"},

    {"id": "first-redemption", "category": "Rewards", "name": "Treat Yourself", "description": "Redeem your first reward", "icon": "Gift", "color": "#EF476F", "target": 1, "metric": "redemptions_count"},
    {"id": "redemptions-10", "category": "Rewards", "name": "Big Spender", "description": "Redeem 10 rewards", "icon": "Gift", "color": "#118AB2", "target": 10, "metric": "redemptions_count"},

    {"id": "quests-10", "category": "Quests", "name": "Quest Champion", "description": "Claim 10 quest rewards", "icon": "Flag", "color": "#BE185D", "target": 10, "metric": "quest_claims_count"},
]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def compute_user_metrics(db, uid: str) -> dict:
    today = today_str()

    habits_count = await db.habits.count_documents({"user_id": uid})
    tasks_total = await db.tasks.count_documents({"user_id": uid})
    tasks_done = await db.tasks.count_documents({"user_id": uid, "completed": True})
    redemptions_count = await db.redemptions.count_documents({"user_id": uid})
    quest_claims_count = await db.quest_claims.count_documents({"user_id": uid})

    pipe = [
        {"$match": {"user_id": uid, "type": "earn"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]

    earn_agg = await db.transactions.aggregate(pipe).to_list(1)
    total_earned = earn_agg[0]["total"] if earn_agg else 0

    habits = await db.habits.find(
        {"user_id": uid},
        {
            "_id": 0,
            "streak": 1,
            "longest_streak": 1,
            "total_completions": 1,
            "completions": 1,
        },
    ).to_list(1000)

    best_streak = max([h.get("longest_streak", 0) for h in habits], default=0)
    current_max_streak = max([h.get("streak", 0) for h in habits], default=0)
    total_habit_completions = sum([h.get("total_completions", 0) for h in habits])
    habits_completed_today = sum(1 for h in habits if today in h.get("completions", []))

    tasks_completed_today = await db.tasks.count_documents({
        "user_id": uid,
        "completed": True,
        "completed_at": {"$gte": today + "T00:00:00+00:00"},
    })

    completed_today = habits_completed_today + tasks_completed_today

    return {
        "habits_count": habits_count,
        "total_habit_completions": total_habit_completions,
        "tasks_total": tasks_total,
        "tasks_done": tasks_done,
        "redemptions_count": redemptions_count,
        "quest_claims_count": quest_claims_count,
        "total_earned": total_earned,
        "best_streak": best_streak,
        "current_max_streak": current_max_streak,
        "completed_today": completed_today,
    }


async def sync_user_achievements(db, uid: str) -> list:
    metrics = await compute_user_metrics(db, uid)

    existing_docs = await db.user_achievements.find(
        {"user_id": uid},
        {"_id": 0, "achievement_id": 1},
    ).to_list(200)

    existing = {doc["achievement_id"] for doc in existing_docs}
    newly_earned = []

    for achievement in ACHIEVEMENT_DEFS:
        achievement_id = achievement["id"]
        progress = int(metrics.get(achievement["metric"], 0))
        target = int(achievement["target"])

        if progress >= target and achievement_id not in existing:
            earned_at = now_utc_iso()

            await db.user_achievements.update_one(
                {"user_id": uid, "achievement_id": achievement_id},
                {
                    "$setOnInsert": {
                        "user_id": uid,
                        "achievement_id": achievement_id,
                        "earned_at": earned_at,
                    }
                },
                upsert=True,
            )

            newly_earned.append(achievement_id)
            existing.add(achievement_id)

    return newly_earned