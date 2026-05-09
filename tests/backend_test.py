"""Backend API tests for Habio."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://habit-rewards-13.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def new_user(session):
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    r = session.post(f"{API}/auth/register", json={"email": email, "password": "test1234", "name": "Tester"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "token" in data and data["user"]["email"] == email
    return {"email": email, "password": "test1234", "token": data["token"], "id": data["user"]["id"]}


@pytest.fixture(scope="module")
def auth(new_user):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {new_user['token']}"})
    return s


# --- Health & Auth ---
def test_health(session):
    r = session.get(f"{API}/")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_admin_login(session):
    r = session.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert r.status_code == 200, r.text
    assert "token" in r.json()


def test_login_invalid(session):
    r = session.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": "wrong"})
    assert r.status_code == 401


def test_register_duplicate(session, new_user):
    r = session.post(f"{API}/auth/register", json={"email": new_user["email"], "password": "x23456"})
    assert r.status_code == 400


def test_me_requires_auth(session):
    r = requests.get(f"{API}/auth/me")
    assert r.status_code == 401


def test_me_ok(auth, new_user):
    r = auth.get(f"{API}/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == new_user["email"]


# --- Habits ---
def test_habit_crud_and_complete(auth):
    # Easy => 5 coins
    r = auth.post(f"{API}/habits", json={"name": "TEST_habit", "difficulty": "easy", "frequency": "daily"})
    assert r.status_code == 200
    h = r.json()
    assert h["coins_per_completion"] == 5
    hid = h["id"]

    # list
    r = auth.get(f"{API}/habits")
    assert r.status_code == 200
    assert any(x["id"] == hid for x in r.json())

    # complete -> +5 coins
    r = auth.post(f"{API}/habits/{hid}/complete")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["coins_earned"] == 5
    assert d["new_balance"] >= 5
    assert d["streak"] == 1

    # double complete blocked
    r = auth.post(f"{API}/habits/{hid}/complete")
    assert r.status_code == 400

    # custom coins override
    r = auth.put(f"{API}/habits/{hid}", json={"name": "TEST_habit2", "difficulty": "hard", "frequency": "daily", "custom_coins": 50})
    assert r.status_code == 200
    assert r.json()["coins_per_completion"] == 50

    # delete
    r = auth.delete(f"{API}/habits/{hid}")
    assert r.status_code == 200


def test_habit_difficulty_coins(auth):
    for diff, coins in [("easy", 5), ("medium", 10), ("hard", 20)]:
        r = auth.post(f"{API}/habits", json={"name": f"TEST_{diff}", "difficulty": diff})
        assert r.status_code == 200
        assert r.json()["coins_per_completion"] == coins
        auth.delete(f"{API}/habits/{r.json()['id']}")


# --- Tasks ---
def test_task_flow(auth):
    r = auth.post(f"{API}/tasks", json={"name": "TEST_task", "difficulty": "medium"})
    assert r.status_code == 200
    t = r.json()
    assert t["coins_reward"] == 10
    tid = t["id"]

    # balance before
    bal_before = auth.get(f"{API}/stats").json()["coin_balance"]

    # complete
    r = auth.post(f"{API}/tasks/{tid}/complete")
    assert r.status_code == 200
    assert r.json()["coins_earned"] == 10
    assert r.json()["new_balance"] == bal_before + 10

    # double complete blocked
    r = auth.post(f"{API}/tasks/{tid}/complete")
    assert r.status_code == 400

    # uncomplete refunds
    r = auth.post(f"{API}/tasks/{tid}/uncomplete")
    assert r.status_code == 200
    assert r.json()["new_balance"] == bal_before

    # update + delete
    r = auth.put(f"{API}/tasks/{tid}", json={"name": "TEST_task_upd", "difficulty": "hard"})
    assert r.status_code == 200 and r.json()["coins_reward"] == 20
    assert auth.delete(f"{API}/tasks/{tid}").status_code == 200


# --- Rewards ---
def test_reward_flow(auth):
    # Earn first
    h = auth.post(f"{API}/habits", json={"name": "TEST_earn", "custom_coins": 100}).json()
    # already completed today habit earlier test? different id so ok
    auth.post(f"{API}/habits/{h['id']}/complete")

    r = auth.post(f"{API}/rewards", json={"name": "TEST_reward", "cost": 30})
    assert r.status_code == 200
    rid = r.json()["id"]

    r = auth.post(f"{API}/rewards/{rid}/redeem")
    assert r.status_code == 200, r.text
    assert "redemption" in r.json()

    # Insufficient: raise cost massively
    r = auth.post(f"{API}/rewards", json={"name": "TEST_big", "cost": 999999})
    big_id = r.json()["id"]
    r = auth.post(f"{API}/rewards/{big_id}/redeem")
    assert r.status_code == 400

    # history
    assert auth.get(f"{API}/redemptions").status_code == 200
    assert auth.get(f"{API}/transactions").status_code == 200

    auth.delete(f"{API}/rewards/{rid}")
    auth.delete(f"{API}/rewards/{big_id}")
    auth.delete(f"{API}/habits/{h['id']}")


def test_stats(auth):
    r = auth.get(f"{API}/stats")
    assert r.status_code == 200
    for k in ["coin_balance", "total_earned", "best_streak", "tasks_done", "habits_count"]:
        assert k in r.json()
