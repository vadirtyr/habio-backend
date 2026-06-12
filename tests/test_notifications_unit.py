import unittest

from notifications import create_notification


class FakeNotifications:
    def __init__(self):
        self.items = []

    async def insert_one(self, item):
        self.items.append(dict(item))


class FakeDatabase:
    def __init__(self):
        self.notifications = FakeNotifications()


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_notification_does_not_require_actor(self):
        db = FakeDatabase()

        result = await create_notification(
            db=db,
            user_id="user-1",
            actor_id=None,
            notification_type="streak_reminder",
            message="Keep your streak going",
            target_id="habit-1",
        )

        self.assertIsNotNone(result)
        self.assertEqual(len(db.notifications.items), 1)
        self.assertIsNone(result["actor_id"])
        self.assertIsNone(result["actor_user_id"])

    async def test_unknown_notification_type_is_rejected(self):
        db = FakeDatabase()

        result = await create_notification(
            db=db,
            user_id="user-1",
            actor_id=None,
            notification_type="unknown",
            message="Ignored",
        )

        self.assertIsNone(result)
        self.assertEqual(db.notifications.items, [])


if __name__ == "__main__":
    unittest.main()
