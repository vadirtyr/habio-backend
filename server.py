from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
import bcrypt
import jwt

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal
from datetime import datetime, timezone, timedelta
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from fastapi.responses import JSONResponse

# --- Config ---

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60 * 24 * 7

DIFFICULTY_COINS = {"easy": 5, "medium": 10, "hard": 20}
XP_PER_COIN = 2

THEME_STORE = {
    "light": {"id": "light", "name": "Daylight", "price": 0, "type": "included"},
    "dark": {"id": "dark", "name": "Midnight", "price": 0, "type": "included"},
    "nature": {"id": "nature", "name": "Evergreen", "price": 0, "type": "included"},
    "focus": {"id": "focus", "name": "Slate", "price": 0, "type": "included"},

    "amoled": {"id": "amoled", "name": "AMOLED", "price": 500, "type": "store"},
    "ocean": {"id": "ocean", "name": "Tidal", "price": 750, "type": "store"},
    "coffee": {"id": "coffee", "name": "Ember", "price": 1000, "type": "store"},
    "solsticeStore": {"id": "solsticeStore", "name": "Solstice", "price": 1250, "type": "store"},

    "forestNight": {"id": "forestNight", "name": "Forest Night", "type": "achievement", "price": 0, "unlockAchievement": "streak-7"},
    "aurora": {"id": "aurora", "name": "Aurora", "type": "achievement", "price": 0, "unlockAchievement": "coins-500"},
    "solstice": {"id": "solstice", "name": "Solstice Crown", "type": "achievement", "price": 0, "unlockAchievement": "tasks-50"},
    "midnightGold": {"id": "midnightGold", "name": "Obsidian Gold", "type": "achievement", "price": 0, "unlockAchievement": "streak-30"},
    "oceanBreeze": {"id": "oceanBreeze", "name": "Ocean Breeze", "type": "achievement", "price": 0, "unlockAchievement": "habits-25"},
    "roseGarden": {"id": "roseGarden", "name": "Rose Garden", "type": "achievement", "price": 0, "unlockAchievement": "quests-10"},
"comet": {
    "id": "comet",
    "name": "Comet",
    "type": "level",
    "price": 0,
    "unlockLevel": 3,
},
"nebula": {
    "id": "nebula",
    "name": "Nebula",
    "type": "level",
    "price": 0,
    "unlockLevel": 5,
},
"eclipse": {
    "id": "eclipse",
    "name": "Eclipse",
    "type": "level",
    "price": 0,
    "unlockLevel": 10,
},
"cosmicGold": {
    "id": "cosmicGold",
    "name": "Cosmic Gold",
    "type": "level",
    "price": 0,
    "unlockLevel": 15,
},

}

DEFAULT_THEMES = ["light", "dark", "nature", "focus"]

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")
JWT_SECRET = os.environ.get("JWT_SECRET")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
if not MONGO_URL:
    raise RuntimeError("Missing required environment variable: MONGO_URL")

if not DB_NAME:
    raise RuntimeError("Missing required environment variable: DB_NAME")

if not JWT_SECRET:
    raise RuntimeError("Missing required environment variable: JWT_SECRET")


# --- DB ---

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]


# --- App ---

app = FastAPI(title="OurOrbit API")
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
api_router = APIRouter(prefix="/api")


@app.get("/")
async def health():
    return {"status": "ok", "service": "ourorbit-api"}


# ============== Helpers ==============

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def coins_for(difficulty: Optional[str], custom_coins: Optional[int]) -> int:
    if custom_coins is not None and int(custom_coins) > 0:
        return int(custom_coins)

    if difficulty and difficulty.lower() in DIFFICULTY_COINS:
        return DIFFICULTY_COINS[difficulty.lower()]

    return 10


def level_for_xp(xp: int) -> int:
    return max(1, int((xp / 100) ** 0.5) + 1)


def xp_needed_for_level(level: int) -> int:
    return ((level - 1) ** 2) * 100


def xp_progress(xp: int) -> dict:
    level = level_for_xp(xp)

    current_level_xp = xp_needed_for_level(level)
    next_level_xp = xp_needed_for_level(level + 1)

    progress = xp - current_level_xp
    needed = next_level_xp - current_level_xp

    return {
        "level": level,
        "current_xp": xp,
        "current_level_xp": current_level_xp,
        "next_level_xp": next_level_xp,
        "progress": progress,
        "needed": needed,
        "percent": int((progress / needed) * 100) if needed > 0 else 100,
    }


def clean_user(u: dict) -> dict:
    xp = u.get("xp", 0)

    return {
        "id": u["id"],
        "email": u["email"],
        "name": u.get("name", ""),
        "coin_balance": u.get("coin_balance", 0),
        "xp": xp,
        "level_data": xp_progress(xp),
        "selected_theme": u.get("selected_theme", "light"),
        "owned_themes": u.get("owned_themes", DEFAULT_THEMES),
        "created_at": u.get("created_at"),
    }


async def get_current_user(request: Request) -> dict:
    token = None

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


async def log_transaction(
    user_id: str,
    amount: int,
    type_: str,
    source: str,
    source_id: str,
    description: str,
):
    tx = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "amount": amount,
        "type": type_,
        "source": source,
        "source_id": source_id,
        "description": description,
        "created_at": now_utc_iso(),
    }

    await db.transactions.insert_one(tx)
    tx.pop("_id", None)
    return tx


async def award_user_xp(user: dict, coins_earned: int) -> dict:
    xp_earned = max(0, int(coins_earned) * XP_PER_COIN)
    old_xp = int(user.get("xp", 0))
    new_xp = old_xp + xp_earned

    old_level = level_for_xp(old_xp)
    new_level = level_for_xp(new_xp)

    return {
        "xp_earned": xp_earned,
        "old_xp": old_xp,
        "new_xp": new_xp,
        "old_level": old_level,
        "new_level": new_level,
        "leveled_up": new_level > old_level,
        "level_data": xp_progress(new_xp),
    }


# ============== Models ==============

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: Optional[str] = Field(default="", max_length=80)

class LoginIn(BaseModel):
    email: EmailStr
    password: str


class HabitIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default="", max_length=300)
    frequency: Literal["daily", "weekly"] = "daily"
    difficulty: Optional[Literal["easy", "medium", "hard"]] = "medium"
    custom_coins: Optional[int] = Field(default=None, ge=1, le=100)
    icon: Optional[str] = Field(default="flame", max_length=40)
    category: Optional[str] = Field(default=None, max_length=60)


class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default="", max_length=300)
    difficulty: Optional[Literal["easy", "medium", "hard"]] = "medium"
    custom_coins: Optional[int] = Field(default=None, ge=1, le=100)
    due_date: Optional[str] = Field(default=None, max_length=40)
    recurrence: Optional[Literal["none", "daily", "weekly"]] = "none"

class RewardIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default="", max_length=300)
    cost: int = Field(gt=0, le=10000)
    icon: Optional[str] = Field(default="gift", max_length=40)


class ThemePurchaseIn(BaseModel):
    theme_id: str


class ThemeSelectIn(BaseModel):
    theme_id: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# ============== Auth Routes ==============

@api_router.post("/auth/register")
@limiter.limit("5/minute")
async def register(
    request: Request,
    body: RegisterIn,
    response: Response,
):
    email = body.email.lower()
    existing = await db.users.find_one({"email": email})

    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())

    user_doc = {
        "id": user_id,
        "email": email,
        "password_hash": hash_password(body.password),
        "name": body.name or email.split("@")[0],
        "coin_balance": 0,
        "xp": 0,
        "selected_theme": "light",
        "owned_themes": DEFAULT_THEMES.copy(),
        "created_at": now_utc_iso(),
    }

    await db.users.insert_one(user_doc)

    token = create_access_token(user_id, email)

    response.set_cookie(
    "access_token",
    token,
    httponly=True,
    secure=ENVIRONMENT == "production",
    samesite="lax",
    max_age=ACCESS_TOKEN_MINUTES * 60,
    path="/",
)

    return {"token": token, "user": clean_user(user_doc)}


@api_router.post("/auth/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginIn,
    response: Response,
):
    email = body.email.lower()
    user = await db.users.find_one({"email": email}, {"_id": 0})

    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"], email)

    response.set_cookie(
    "access_token",
    token,
    httponly=True,
    secure=ENVIRONMENT == "production",
    samesite="lax",
    max_age=ACCESS_TOKEN_MINUTES * 60,
    path="/",
)

    return {"token": token, "user": clean_user(user)}


@api_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return clean_user(user)


@api_router.post("/auth/change-password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
):
    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 8 characters",
        )

    fresh_user = await db.users.find_one({"id": user["id"]})

    if not fresh_user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(payload.current_password, fresh_user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    new_hash = hash_password(payload.new_password)

    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": new_hash}},
    )

    return {"ok": True, "message": "Password changed successfully"}


@api_router.delete("/auth/me")
async def delete_account(
    response: Response,
    user: dict = Depends(get_current_user),
):
    uid = user["id"]

    await db.habits.delete_many({"user_id": uid})
    await db.tasks.delete_many({"user_id": uid})
    await db.rewards.delete_many({"user_id": uid})
    await db.redemptions.delete_many({"user_id": uid})
    await db.transactions.delete_many({"user_id": uid})
    await db.user_achievements.delete_many({"user_id": uid})
    await db.quest_claims.delete_many({"user_id": uid})

    await db.users.delete_one({"id": uid})

    response.delete_cookie("access_token", path="/")

    return {"ok": True, "message": "Account deleted successfully"}


# ============== Habits ==============

@api_router.get("/habits")
async def list_habits(user: dict = Depends(get_current_user)):
    items = await db.habits.find({"user_id": user["id"]}, {"_id": 0}).to_list(1000)
    today = today_str()

    for h in items:
        h["completed_today"] = today in h.get("completions", [])

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


@api_router.post("/habits")
async def create_habit(body: HabitIn, user: dict = Depends(get_current_user)):
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": body.name,
        "description": body.description or "",
        "frequency": body.frequency,
        "difficulty": body.difficulty,
        "custom_coins": body.custom_coins,
        "coins_per_completion": coins_for(body.difficulty, body.custom_coins),
        "icon": body.icon or "flame",
        "category": body.category,
        "streak": 0,
        "longest_streak": 0,
        "last_completed_date": None,
        "completions": [],
        "total_completions": 0,
        "created_at": now_utc_iso(),
    }

    await db.habits.insert_one(doc)
    doc.pop("_id", None)
    doc["completed_today"] = False

    await sync_user_achievements(user["id"])

    return doc


@api_router.put("/habits/{habit_id}")
async def update_habit(habit_id: str, body: HabitIn, user: dict = Depends(get_current_user)):
    update = {
        "name": body.name,
        "description": body.description or "",
        "frequency": body.frequency,
        "difficulty": body.difficulty,
        "custom_coins": body.custom_coins,
        "coins_per_completion": coins_for(body.difficulty, body.custom_coins),
        "icon": body.icon or "flame",
        "category": body.category,
    }

    result = await db.habits.update_one(
        {"id": habit_id, "user_id": user["id"]},
        {"$set": update},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Habit not found")

    updated = await db.habits.find_one({"id": habit_id}, {"_id": 0})
    updated["completed_today"] = today_str() in updated.get("completions", [])

    return updated


@api_router.delete("/habits/{habit_id}")
async def delete_habit(habit_id: str, user: dict = Depends(get_current_user)):
    result = await db.habits.delete_one({"id": habit_id, "user_id": user["id"]})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Habit not found")

    return {"ok": True}


@api_router.post("/habits/{habit_id}/complete")
async def complete_habit(habit_id: str, user: dict = Depends(get_current_user)):
    habit = await db.habits.find_one(
        {"id": habit_id, "user_id": user["id"]},
        {"_id": 0},
    )

    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")

    today = today_str()
    completions = habit.get("completions", [])

    if today in completions:
        raise HTTPException(status_code=400, detail="Already completed today")

    last = habit.get("last_completed_date")
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    new_streak = 1

    if last == yesterday:
        new_streak = habit.get("streak", 0) + 1
    elif last == today:
        new_streak = habit.get("streak", 0)

    longest = max(habit.get("longest_streak", 0), new_streak)

    base_coins = habit.get("coins_per_completion") or coins_for(
        habit.get("difficulty"),
        habit.get("custom_coins"),
    )

    def streak_bonus(streak: int) -> int:
        if streak >= 30:
            return 75
        if streak >= 14:
            return 30
        if streak >= 7:
            return 15
        if streak >= 3:
            return 5
        return 0

    bonus = streak_bonus(new_streak)
    coins = base_coins + bonus
    xp_data = await award_user_xp(user, coins)

    completions.append(today)

    await db.habits.update_one(
        {"id": habit_id},
        {
            "$set": {
                "completions": completions,
                "last_completed_date": today,
                "streak": new_streak,
                "longest_streak": longest,
            },
            "$inc": {"total_completions": 1},
        },
    )

    new_balance = user.get("coin_balance", 0) + coins

    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "coin_balance": new_balance,
                "xp": xp_data["new_xp"],
            }
        },
    )

    desc = f"Completed habit: {habit['name']}"
    if bonus > 0:
        desc += f" (+{bonus} streak bonus)"

    await log_transaction(user["id"], coins, "earn", "habit", habit_id, desc)

    newly_earned = await sync_user_achievements(user["id"])

    updated = await db.habits.find_one({"id": habit_id}, {"_id": 0})
    updated["completed_today"] = True

    return {
        "habit": updated,
        "coins_earned": coins,
        "base_coins": base_coins,
        "streak_bonus": bonus,
        "new_balance": new_balance,
        "streak": new_streak,
        "xp_earned": xp_data["xp_earned"],
        "level_data": xp_data["level_data"],
        "leveled_up": xp_data["leveled_up"],
        "old_level": xp_data["old_level"],
        "new_level": xp_data["new_level"],
        "new_achievements": newly_earned,
    }


# ============== Tasks ==============

@api_router.get("/tasks")
async def list_tasks(user: dict = Depends(get_current_user)):
    items = await db.tasks.find({"user_id": user["id"]}, {"_id": 0}).to_list(1000)

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    items.sort(key=lambda x: x.get("completed", False))

    return items


@api_router.post("/tasks")
async def create_task(body: TaskIn, user: dict = Depends(get_current_user)):
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": body.name,
        "description": body.description or "",
        "difficulty": body.difficulty,
        "custom_coins": body.custom_coins,
        "coins_reward": coins_for(body.difficulty, body.custom_coins),
        "due_date": body.due_date,
        "recurrence": body.recurrence or "none",
        "completed": False,
        "completed_at": None,
        "created_at": now_utc_iso(),
    }

    await db.tasks.insert_one(doc)
    doc.pop("_id", None)

    await sync_user_achievements(user["id"])

    return doc


@api_router.put("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskIn, user: dict = Depends(get_current_user)):
    update = {
        "name": body.name,
        "description": body.description or "",
        "difficulty": body.difficulty,
        "custom_coins": body.custom_coins,
        "coins_reward": coins_for(body.difficulty, body.custom_coins),
        "due_date": body.due_date,
        "recurrence": body.recurrence or "none",
    }

    result = await db.tasks.update_one(
        {"id": task_id, "user_id": user["id"]},
        {"$set": update},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")

    return await db.tasks.find_one({"id": task_id}, {"_id": 0})


@api_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, user: dict = Depends(get_current_user)):
    result = await db.tasks.delete_one({"id": task_id, "user_id": user["id"]})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"ok": True}


@api_router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: str, user: dict = Depends(get_current_user)):
    task = await db.tasks.find_one(
        {"id": task_id, "user_id": user["id"]},
        {"_id": 0},
    )

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("completed"):
        raise HTTPException(status_code=400, detail="Task already completed")

    coins = task.get("coins_reward") or coins_for(
        task.get("difficulty"),
        task.get("custom_coins"),
    )

    xp_data = await award_user_xp(user, coins)

    await db.tasks.update_one(
        {"id": task_id},
        {"$set": {"completed": True, "completed_at": now_utc_iso()}},
    )

    new_balance = user.get("coin_balance", 0) + coins

    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "coin_balance": new_balance,
                "xp": xp_data["new_xp"],
            }
        },
    )

    await log_transaction(
        user["id"],
        coins,
        "earn",
        "task",
        task_id,
        f"Completed task: {task['name']}",
    )

    next_task_id = None
    recurrence = task.get("recurrence", "none")

    if recurrence in ("daily", "weekly"):
        delta_days = 1 if recurrence == "daily" else 7
        next_due = (datetime.now(timezone.utc).date() + timedelta(days=delta_days)).isoformat()
        next_task_id = str(uuid.uuid4())

        await db.tasks.insert_one({
            "id": next_task_id,
            "user_id": user["id"],
            "name": task["name"],
            "description": task.get("description", ""),
            "difficulty": task.get("difficulty"),
            "custom_coins": task.get("custom_coins"),
            "coins_reward": task.get("coins_reward"),
            "due_date": next_due,
            "recurrence": recurrence,
            "completed": False,
            "completed_at": None,
            "created_at": now_utc_iso(),
        })

    newly_earned = await sync_user_achievements(user["id"])

    return {
        "coins_earned": coins,
        "new_balance": new_balance,
        "next_task_id": next_task_id,
        "xp_earned": xp_data["xp_earned"],
        "level_data": xp_data["level_data"],
        "leveled_up": xp_data["leveled_up"],
        "old_level": xp_data["old_level"],
        "new_level": xp_data["new_level"],
        "new_achievements": newly_earned,
    }


@api_router.post("/tasks/{task_id}/uncomplete")
async def uncomplete_task(task_id: str, user: dict = Depends(get_current_user)):
    task = await db.tasks.find_one(
        {"id": task_id, "user_id": user["id"]},
        {"_id": 0},
    )

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not task.get("completed"):
        raise HTTPException(status_code=400, detail="Task not completed")

    coins = task.get("coins_reward") or coins_for(
        task.get("difficulty"),
        task.get("custom_coins"),
    )

    await db.tasks.update_one(
        {"id": task_id},
        {"$set": {"completed": False, "completed_at": None}},
    )

    new_balance = max(0, user.get("coin_balance", 0) - coins)

    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"coin_balance": new_balance}},
    )

    await log_transaction(
        user["id"],
        -coins,
        "spend",
        "task_undo",
        task_id,
        f"Un-completed task: {task['name']}",
    )

    return {"coins_refunded": -coins, "new_balance": new_balance}


# ============== Rewards ==============

@api_router.get("/rewards")
async def list_rewards(user: dict = Depends(get_current_user)):
    items = await db.rewards.find({"user_id": user["id"]}, {"_id": 0}).to_list(1000)
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


@api_router.post("/rewards")
async def create_reward(body: RewardIn, user: dict = Depends(get_current_user)):
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": body.name,
        "description": body.description or "",
        "cost": int(body.cost),
        "icon": body.icon or "gift",
        "times_redeemed": 0,
        "created_at": now_utc_iso(),
    }

    await db.rewards.insert_one(doc)
    doc.pop("_id", None)

    return doc


@api_router.put("/rewards/{reward_id}")
async def update_reward(reward_id: str, body: RewardIn, user: dict = Depends(get_current_user)):
    result = await db.rewards.update_one(
        {"id": reward_id, "user_id": user["id"]},
        {
            "$set": {
                "name": body.name,
                "description": body.description or "",
                "cost": int(body.cost),
                "icon": body.icon or "gift",
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Reward not found")

    return await db.rewards.find_one({"id": reward_id}, {"_id": 0})


@api_router.delete("/rewards/{reward_id}")
async def delete_reward(reward_id: str, user: dict = Depends(get_current_user)):
    result = await db.rewards.delete_one({"id": reward_id, "user_id": user["id"]})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Reward not found")

    return {"ok": True}


@api_router.post("/rewards/{reward_id}/redeem")
async def redeem_reward(reward_id: str, user: dict = Depends(get_current_user)):
    reward = await db.rewards.find_one(
        {"id": reward_id, "user_id": user["id"]},
        {"_id": 0},
    )

    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")

    cost = int(reward["cost"])
    balance = user.get("coin_balance", 0)

    if balance < cost:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough coins. Need {cost - balance} more.",
        )

    new_balance = balance - cost

    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"coin_balance": new_balance}},
    )

    await db.rewards.update_one(
        {"id": reward_id},
        {"$inc": {"times_redeemed": 1}},
    )

    redemption = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "reward_id": reward_id,
        "reward_name": reward["name"],
        "reward_icon": reward.get("icon", "gift"),
        "cost": cost,
        "redeemed_at": now_utc_iso(),
    }

    await db.redemptions.insert_one(redemption)
    redemption.pop("_id", None)

    await log_transaction(
        user["id"],
        -cost,
        "spend",
        "reward",
        reward_id,
        f"Redeemed: {reward['name']}",
    )

    newly_earned = await sync_user_achievements(user["id"])

    return {
        "redemption": redemption,
        "new_balance": new_balance,
        "new_achievements": newly_earned,
    }


@api_router.get("/redemptions")
async def list_redemptions(user: dict = Depends(get_current_user)):
    items = await db.redemptions.find({"user_id": user["id"]}, {"_id": 0}).to_list(1000)
    items.sort(key=lambda x: x.get("redeemed_at", ""), reverse=True)
    return items


# ============== Transactions / Stats ==============

@api_router.get("/transactions")
async def list_transactions(user: dict = Depends(get_current_user)):
    items = await db.transactions.find({"user_id": user["id"]}, {"_id": 0}).to_list(1000)
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return items


@api_router.get("/stats")
async def get_stats(user: dict = Depends(get_current_user)):
    uid = user["id"]
    metrics = await compute_user_metrics(uid)
    rewards_count = await db.rewards.count_documents({"user_id": uid})
    xp = user.get("xp", 0)

    return {
        "coin_balance": user.get("coin_balance", 0),
        "xp": xp,
        "level_data": xp_progress(xp),
        "total_earned": metrics["total_earned"],
        "habits_count": metrics["habits_count"],
        "total_habit_completions": metrics["total_habit_completions"],
        "tasks_total": metrics["tasks_total"],
        "tasks_done": metrics["tasks_done"],
        "tasks_pending": metrics["tasks_total"] - metrics["tasks_done"],
        "rewards_count": rewards_count,
        "redemptions_count": metrics["redemptions_count"],
        "quest_claims_count": metrics["quest_claims_count"],
        "best_streak": metrics["best_streak"],
        "current_max_streak": metrics["current_max_streak"],
        "completed_today": metrics["completed_today"],
        "streak_days": metrics["current_max_streak"],
        "total_habits": metrics["habits_count"],
        "total_tasks": metrics["tasks_total"],
    }


# ============== Achievements ==============

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


async def compute_user_metrics(uid: str) -> dict:
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
        {"_id": 0, "streak": 1, "longest_streak": 1, "total_completions": 1, "completions": 1},
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


async def sync_user_achievements(uid: str) -> list:
    metrics = await compute_user_metrics(uid)

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


@api_router.get("/achievements")
async def list_achievements(user: dict = Depends(get_current_user)):
    uid = user["id"]
    newly_earned_ids = await sync_user_achievements(uid)
    metrics = await compute_user_metrics(uid)

    existing_docs = await db.user_achievements.find(
        {"user_id": uid},
        {"_id": 0},
    ).to_list(200)

    existing = {d["achievement_id"]: d for d in existing_docs}
    items = []

    for a in ACHIEVEMENT_DEFS:
        progress = int(metrics.get(a["metric"], 0))
        target = int(a["target"])
        earned = progress >= target
        earned_at = existing.get(a["id"], {}).get("earned_at")

        items.append({
            **a,
            "progress": min(progress, target),
            "raw_progress": progress,
            "earned": earned,
            "earned_at": earned_at,
            "newly_earned": a["id"] in newly_earned_ids,
            "percent": int(min(100, (progress / target) * 100)) if target else 0,
        })

    items.sort(
        key=lambda x: (
            x["earned"],
            -(x.get("percent", 0)),
        )
    )

    earned_count = sum(1 for x in items if x["earned"])
    next_unlock = next((x for x in items if not x["earned"]), None)

    return {
        "items": items,
        "earned_count": earned_count,
        "total": len(items),
        "next_unlock": next_unlock,
    }


# ============== Quests ==============

QUEST_DEFS = [
    {"id": "daily-3-habits", "name": "Habit Hat-Trick", "description": "Complete 3 habits today", "icon": "Flame", "period": "daily", "target": 3, "metric": "habits_today", "reward": 25},
    {"id": "daily-task", "name": "Get One Done", "description": "Complete any task today", "icon": "Check", "period": "daily", "target": 1, "metric": "tasks_today", "reward": 15},
    {"id": "weekly-10-habits", "name": "Habit Streaker", "description": "Complete 10 habits this week", "icon": "Zap", "period": "weekly", "target": 10, "metric": "habits_this_week", "reward": 50},
    {"id": "weekly-3-tasks", "name": "Task Master", "description": "Complete 3 tasks this week", "icon": "Award", "period": "weekly", "target": 3, "metric": "tasks_this_week", "reward": 40},
]


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


async def compute_quest_metrics(uid: str) -> dict:
    today = today_str()
    monday = week_start_iso()

    habits = await db.habits.find(
        {"user_id": uid},
        {"_id": 0, "completions": 1},
    ).to_list(1000)

    habits_today = sum(1 for h in habits if today in h.get("completions", []))

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


@api_router.get("/quests")
async def list_quests(user: dict = Depends(get_current_user)):
    uid = user["id"]
    metrics = await compute_quest_metrics(uid)

    claims_docs = await db.quest_claims.find(
        {"user_id": uid},
        {"_id": 0},
    ).to_list(200)

    claims = {(c["quest_id"], c["period_key"]) for c in claims_docs}

    items = []

    for q in QUEST_DEFS:
        progress = int(metrics.get(q["metric"], 0))
        target = int(q["target"])
        period_key = get_period_key(q["period"])
        completed = progress >= target
        claimed = (q["id"], period_key) in claims

        items.append({
            **q,
            "period_key": period_key,
            "progress": min(progress, target),
            "raw_progress": progress,
            "percent": int(min(100, (progress / target) * 100)) if target else 0,
            "completed": completed,
            "claimed": claimed,
            "claimable": completed and not claimed,
        })

    return {"items": items}


@api_router.post("/quests/{quest_id}/claim")
async def claim_quest(quest_id: str, user: dict = Depends(get_current_user)):
    quest = next((q for q in QUEST_DEFS if q["id"] == quest_id), None)

    if not quest:
        raise HTTPException(status_code=404, detail="Quest not found")

    uid = user["id"]
    period_key = get_period_key(quest["period"])

    already = await db.quest_claims.find_one({
        "user_id": uid,
        "quest_id": quest_id,
        "period_key": period_key,
    })

    if already:
        raise HTTPException(status_code=400, detail="Already claimed for this period")

    metrics = await compute_quest_metrics(uid)
    progress = int(metrics.get(quest["metric"], 0))

    if progress < int(quest["target"]):
        raise HTTPException(status_code=400, detail="Quest not completed yet")

    reward = int(quest["reward"])
    xp_data = await award_user_xp(user, reward)
    new_balance = user.get("coin_balance", 0) + reward

    await db.users.update_one(
        {"id": uid},
        {
            "$set": {
                "coin_balance": new_balance,
                "xp": xp_data["new_xp"],
            }
        },
    )

    await db.quest_claims.insert_one({
        "user_id": uid,
        "quest_id": quest_id,
        "period_key": period_key,
        "claimed_at": now_utc_iso(),
    })

    await log_transaction(
        uid,
        reward,
        "earn",
        "quest",
        quest_id,
        f"Quest reward: {quest['name']}",
    )

    newly_earned = await sync_user_achievements(uid)

    return {
        "coins_earned": reward,
        "new_balance": new_balance,
        "quest_id": quest_id,
        "xp_earned": xp_data["xp_earned"],
        "level_data": xp_data["level_data"],
        "leveled_up": xp_data["leveled_up"],
        "old_level": xp_data["old_level"],
        "new_level": xp_data["new_level"],
        "new_achievements": newly_earned,
    }


# ============== Themes ==============

@api_router.get("/themes/me")
async def get_my_themes(user: dict = Depends(get_current_user)):
    uid = user["id"]

    await sync_user_achievements(uid)

    fresh_user = await db.users.find_one({"id": uid}, {"_id": 0})

    if not fresh_user:
        raise HTTPException(status_code=404, detail="User not found")

    owned = fresh_user.get("owned_themes", DEFAULT_THEMES.copy())
    selected = fresh_user.get("selected_theme", "light")
    unlocked_now = []

    level_data = xp_progress(fresh_user.get("xp", 0))
    current_level = level_data["level"]

    for theme_id, theme in THEME_STORE.items():
        if theme.get("type") != "level":
            continue

        required_level = int(theme.get("unlockLevel", 999))

        if current_level >= required_level and theme_id not in owned:
            owned.append(theme_id)
            unlocked_now.append(theme_id)

    earned_docs = await db.user_achievements.find(
        {"user_id": uid},
        {"_id": 0, "achievement_id": 1},
    ).to_list(200)

    earned_ids = {doc["achievement_id"] for doc in earned_docs}

    for theme_id, theme in THEME_STORE.items():
        if theme.get("type") != "achievement":
            continue

        required = theme.get("unlockAchievement")

        if required in earned_ids and theme_id not in owned:
            owned.append(theme_id)
            unlocked_now.append(theme_id)

    if unlocked_now:
        await db.users.update_one(
            {"id": uid},
            {"$set": {"owned_themes": owned}},
        )

    return {
        "owned_themes": owned,
        "selected_theme": selected,
        "unlocked_now": unlocked_now,
        "store": list(THEME_STORE.values()),
        "level_data": level_data,
    }

@api_router.post("/themes/select")
async def select_theme(
    body: ThemeSelectIn,
    user: dict = Depends(get_current_user),
):
    theme_id = body.theme_id

    if theme_id not in THEME_STORE:
        raise HTTPException(status_code=404, detail="Theme not found")

    owned = user.get("owned_themes", DEFAULT_THEMES)

    if theme_id not in owned:
        raise HTTPException(status_code=403, detail="Theme not owned")

    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"selected_theme": theme_id}},
    )

    return {"ok": True, "selected_theme": theme_id}


@api_router.post("/themes/purchase")
async def purchase_theme(
    body: ThemePurchaseIn,
    user: dict = Depends(get_current_user),
):
    theme_id = body.theme_id

    if theme_id not in THEME_STORE:
        raise HTTPException(status_code=404, detail="Theme not found")

    theme = THEME_STORE[theme_id]

    if theme["type"] != "store":
        raise HTTPException(status_code=400, detail="This theme cannot be purchased")

    owned = user.get("owned_themes", DEFAULT_THEMES)

    if theme_id in owned:
        raise HTTPException(status_code=400, detail="Theme already owned")

    balance = user.get("coin_balance", 0)
    price = int(theme["price"])

    if balance < price:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough coins. Need {price - balance} more.",
        )

    new_balance = balance - price
    updated_owned = owned + [theme_id]

    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "coin_balance": new_balance,
                "owned_themes": updated_owned,
            }
        },
    )

    await log_transaction(
        user["id"],
        -price,
        "spend",
        "theme",
        theme_id,
        f"Purchased theme: {theme['name']}",
    )

    return {
        "ok": True,
        "theme_id": theme_id,
        "owned_themes": updated_owned,
        "new_balance": new_balance,
    }


# --- API Health ---

@api_router.get("/")
async def api_health():
    return {"message": "OurOrbit API", "status": "ok"}

@api_router.get("/ready")
async def readiness():
    try:
        await db.command("ping")
        return {"status": "ready"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please slow down."},
    )

# --- Register Router & CORS ---

if ENVIRONMENT == "production":
    ALLOWED_ORIGINS = [
        "https://habioapp.co",
        "https://www.habioapp.co",
        "https://main.dsrkbok7uhqk.amplifyapp.com",
    ]
else:
    ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://192.168.1.43:3000",
        "https://habioapp.co",
        "https://www.habioapp.co",
        "https://main.dsrkbok7uhqk.amplifyapp.com",
    ]
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SlowAPIMiddleware)

# --- Logging ---

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# --- Startup / Shutdown ---

@app.on_event("startup")
async def on_startup():
    await db.users.create_index("email", unique=True)
    await db.habits.create_index("user_id")
    await db.tasks.create_index("user_id")
    await db.rewards.create_index("user_id")
    await db.redemptions.create_index("user_id")
    await db.transactions.create_index("user_id")
    await db.habits.create_index([("user_id", 1), ("created_at", -1)])
    await db.habits.create_index([("user_id", 1), ("last_completed_date", -1)])
    await db.habits.create_index([("user_id", 1), ("total_completions", -1)])

    await db.tasks.create_index([("user_id", 1), ("completed", 1)])
    await db.tasks.create_index([("user_id", 1), ("completed_at", -1)])
    await db.tasks.create_index([("user_id", 1), ("due_date", 1)])
    await db.tasks.create_index([("user_id", 1), ("created_at", -1)])

    await db.rewards.create_index([("user_id", 1), ("created_at", -1)])

    await db.redemptions.create_index([("user_id", 1), ("redeemed_at", -1)])

    await db.transactions.create_index([("user_id", 1), ("created_at", -1)])
    await db.transactions.create_index([("user_id", 1), ("type", 1)])
    await db.transactions.create_index([("user_id", 1), ("source", 1)])

    await db.quest_claims.create_index([("user_id", 1), ("claimed_at", -1)])
    await db.user_achievements.create_index(
        [("user_id", 1), ("achievement_id", 1)],
        unique=True,
    )
    await db.quest_claims.create_index(
        [("user_id", 1), ("quest_id", 1), ("period_key", 1)],
        unique=True,
    )

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com").lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")

    existing = await db.users.find_one({"email": admin_email})

    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Admin",
            "coin_balance": 0,
            "xp": 0,
            "selected_theme": "light",
            "owned_themes": DEFAULT_THEMES.copy(),
            "created_at": now_utc_iso(),
        })

        logger.info(f"Seeded admin user: {admin_email}")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()