from datetime import datetime, timezone
from uuid import uuid4


NOTIFICATION_TYPES = {
    "followed_you",
    "achievement_unlocked",
    "level_up",
    "streak_milestone",
    "quest_completed",
    "reward_unlocked",
    "like_received",
    "cheer_received",
    "weekly_recap",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


async def create_notification(
    db,
    user_id: str,
    actor_id: str,
    notification_type: str,
    message: str,
    target_id: str | None = None,
):
    if not user_id or not actor_id:
        return None

    if notification_type not in NOTIFICATION_TYPES:
        return None

    notification = {
        "id": str(uuid4()),
        "user_id": user_id,
        "actor_id": actor_id,
        "type": notification_type,
        "message": message,
        "target_id": target_id,
        "read": False,
        "created_at": utc_now(),
    }

    await db.notifications.insert_one(notification)

    notification.pop("_id", None)

    return notification


async def notify_followers(
    db,
    actor_id: str,
    notification_type: str,
    message: str,
    target_id: str | None = None,
):
    if notification_type not in NOTIFICATION_TYPES:
        return 0

    actor = await db.users.find_one(
        {"id": actor_id},
        {"_id": 0, "followers": 1},
    )

    if not actor:
        return 0

    follower_ids = actor.get("followers", [])

    notifications = []

    for follower_id in follower_ids:
        if not follower_id or follower_id == actor_id:
            continue

        notifications.append(
            {
                "id": str(uuid4()),
                "user_id": follower_id,
                "actor_id": actor_id,
                "type": notification_type,
                "message": message,
                "target_id": target_id,
                "read": False,
                "created_at": utc_now(),
            }
        )

    if notifications:
        await db.notifications.insert_many(notifications)

    return len(notifications)