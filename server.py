from pyexpat.errors import messages

from resend import response

from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import uuid
import logging
import bcrypt
import jwt
import secrets
import asyncio
import re
import httpx


from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal
from datetime import datetime, timezone, timedelta
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from fastapi.responses import JSONResponse
from email_service import send_password_reset_email
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from xp import XP_PER_COIN, award_user_xp, xp_progress

from achievements import (
    ACHIEVEMENT_DEFS,
    compute_user_metrics,
    sync_user_achievements,
)
from quests import (
    QUEST_DEFS,
    get_period_key,
    compute_quest_metrics,
)
import requests
from jwt import PyJWKClient
from notifications import create_notification, notify_followers

# --- Config ---

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60 * 24 * 7

DIFFICULTY_COINS = {"easy": 5, "medium": 10, "hard": 20}
GOOGLE_WEB_CLIENT_ID = os.environ.get("GOOGLE_WEB_CLIENT_ID")
GOOGLE_IOS_CLIENT_ID = os.environ.get("GOOGLE_IOS_CLIENT_ID")
GOOGLE_ANDROID_CLIENT_ID = os.environ.get("GOOGLE_ANDROID_CLIENT_ID")

APPLE_BUNDLE_ID = os.environ.get("APPLE_BUNDLE_ID")
APPLE_SERVICE_ID = os.environ.get("APPLE_SERVICE_ID")
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

AVATAR_STORE = {
    "explorer": {
        "id": "explorer",
        "name": "Explorer",
        "icon": "compass-outline",
        "type": "included",
    },
    "rocketPilot": {
    "id": "rocketPilot",
    "name": "Rocket Pilot",
    "icon": "rocket-launch",
    "type": "achievement",
    "unlockAchievement": "tasks-50",
    "unlockText": "Complete 50 tasks",
},
"planetKeeper": {
    "id": "planetKeeper",
    "name": "Planet Keeper",
    "icon": "earth",
    "type": "achievement",
    "unlockAchievement": "habits-25",
    "unlockText": "Create 25 habits",
},
"starCaptain": {
    "id": "starCaptain",
    "name": "Star Captain",
    "icon": "shield-star-outline",
    "type": "achievement",
    "unlockAchievement": "coins-500",
    "unlockText": "Earn 500 coins",
},
"cosmicOwl": {
    "id": "cosmicOwl",
    "name": "Cosmic Owl",
    "icon": "owl",
    "type": "achievement",
    "unlockAchievement": "quests-10",
    "unlockText": "Complete 10 quests",
},
}

DEFAULT_THEMES = ["light", "dark", "nature", "focus"]
DEFAULT_AVATARS = ["explorer"]

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


def create_access_token(user_id: str, email: str, token_version: int = 0) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
        "ver": token_version,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def build_auth_response(user: dict, response: Response):
    token = create_access_token(
        user["id"],
        user["email"],
        int(user.get("token_version", 0)),
    )

    response.set_cookie(
        "access_token",
        token,
        httponly=True,
        secure=ENVIRONMENT == "production",
        samesite="lax",
        max_age=ACCESS_TOKEN_MINUTES * 60,
        path="/",
    )

    return {
        "token": token,
        "user": clean_user(user),
    }

def create_reset_token() -> str:
    return secrets.token_urlsafe(48)

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def create_activity(
    user_id: str,
    activity_type: str,
    **payload,
):
    user = await db.users.find_one(
        {"id": user_id},
        {"_id": 0},
    )

    activity = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "username": user.get("username", "") if user else "",
        "display_name": (
            user.get("display_name")
            or user.get("name", "")
            if user
            else ""
        ),
        "avatar": user.get("avatar", "explorer") if user else "explorer",
        "type": activity_type,
        "created_at": now_utc_iso(),
        **payload,
    }

    await db.activity_feed.insert_one(activity)

    activity.pop("_id", None)

    return activity

async def attach_reaction_summaries(
    items: list,
    user_id: Optional[str] = None,
):
    activity_ids = [
        item.get("id")
        for item in items
        if item.get("id")
    ]

    if not activity_ids:
        return items

    reactions = await db.activity_reactions.find(
        {
            "activity_id": {
                "$in": activity_ids,
            }
        },
        {"_id": 0},
    ).to_list(1000)

    summary = {}

    for reaction in reactions:
        activity_id = reaction["activity_id"]
        reaction_type = reaction["reaction"]

        if activity_id not in summary:
            summary[activity_id] = {
                "like": 0,
                "cheer": 0,
                "viewer_reactions": [],
            }

        if reaction_type in ("like", "cheer"):
            summary[activity_id][reaction_type] += 1

        if user_id and reaction["user_id"] == user_id:
            summary[activity_id]["viewer_reactions"].append(reaction_type)

    for item in items:
        item["reactions"] = summary.get(
            item.get("id"),
            {
                "like": 0,
                "cheer": 0,
                "viewer_reactions": [],
            },
        )

    return items

async def send_push_notification(
    user_id: str,
    title: str,
    body: str,
    data: dict | None = None,
):
    tokens = await db.push_tokens.find(
        {"user_id": user_id}
    ).to_list(length=20)

    if not tokens:
        return

    messages = []

    for token_doc in tokens:
        token = token_doc.get("token")

        if not token:
            continue

        messages.append({
            "to": token,
            "sound": "default",
            "title": title,
            "body": body,
            "data": data or {},
        })

    if not messages:
        return

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://exp.host/--/api/v2/push/send",
                json=messages,
            )

        response.raise_for_status()

    except Exception as exc:
        logger.warning("Push notification delivery failed: %s", exc)
async def create_achievement_activities(
    user_id: str,
    achievement_ids: list,
):
    for achievement_id in achievement_ids or []:
        achievement = next(
            (
                item
                for item in ACHIEVEMENT_DEFS
                if item["id"] == achievement_id
            ),
            None,
        )

        if not achievement:
            continue

        await create_activity(
            user_id,
            "achievement_unlock",
            achievement_id=achievement["id"],
            achievement_name=achievement["name"],
        )

        await create_notification(
            db=db,
            user_id=user_id,
            actor_id=None,
            notification_type="achievement_unlock",
            message=f'Achievement unlocked: {achievement["name"]}',
            target_id=achievement["id"],
        )

        await send_push_notification(
            user_id=user_id,
            title="🏆 Achievement Unlocked!",
            body=f'You unlocked {achievement["name"]}',
            data={
                "type": "achievement_unlock",
                "achievement_id": achievement["id"],
            },
        )

        user = await db.users.find_one(
            {"id": user_id},
            {"display_name": 1, "name": 1, "_id": 0},
        )

        await create_friend_activity_notifications(
            actor_user_id=user_id,
            activity_type="achievement_unlock",
            message=f'{user.get("display_name") or user.get("name")} unlocked {achievement["name"]}',
            metadata={
                "achievement_id": achievement["id"],
                "achievement_name": achievement["name"],
            },
        )

def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def coins_for(difficulty: Optional[str], custom_coins: Optional[int]) -> int:
    if custom_coins is not None and int(custom_coins) > 0:
        return int(custom_coins)

    if difficulty and difficulty.lower() in DIFFICULTY_COINS:
        return DIFFICULTY_COINS[difficulty.lower()]

    return 10


def clean_user(u: dict) -> dict:
    xp = u.get("xp", 0)

    return {
        "id": u["id"],
        "email": u["email"],
        "name": u.get("name", ""),
        "username": u.get("username", ""),
        "display_name": u.get("display_name") or u.get("name", ""),
        "bio": u.get("bio", ""),
        "is_public": u.get("is_public", True),
        "avatar": u.get("avatar", "explorer"),
        "owned_avatars": u.get(
            "owned_avatars",
            DEFAULT_AVATARS.copy(),
        ),
        "followers_count": len(u.get("followers", [])),
        "following_count": len(u.get("following", [])),
        "auth_providers": u.get("auth_providers", ["password"]),
        "coin_balance": u.get("coin_balance", 0),
        "xp": xp,
        "level_data": xp_progress(xp),
        "selected_theme": u.get("selected_theme", "light"),
        "owned_themes": u.get("owned_themes", DEFAULT_THEMES.copy()),
        "onboarding_completed": u.get("onboarding_completed", False),
        "created_at": u.get("created_at"),
    }


async def sync_user_avatars(db, user_id: str):
    user = await db.users.find_one({"id": user_id})

    if not user:
        return []

    owned = user.get(
        "owned_avatars",
        DEFAULT_AVATARS.copy(),
    )

    earned_docs = await db.user_achievements.find(
        {"user_id": user_id},
        {"_id": 0, "achievement_id": 1},
    ).to_list(200)

    earned_ids = {
        doc["achievement_id"]
        for doc in earned_docs
    }

    unlocked_now = []

    for avatar_id, avatar in AVATAR_STORE.items():
        if avatar.get("type") != "achievement":
            continue

        required = avatar.get("unlockAchievement")

        if (
            required in earned_ids
            and avatar_id not in owned
        ):
            owned.append(avatar_id)
            unlocked_now.append(avatar)

            await create_activity(
                user_id,
                "avatar_unlock",
                avatar_id=avatar_id,
                avatar_name=avatar["name"],
            )

    if unlocked_now:
        await db.users.update_one(
            {"id": user_id},
            {
                "$set": {
                    "owned_avatars": owned,
                }
            },
        )

    return unlocked_now


async def clean_profile(u: dict) -> dict:
    uid = u["id"]

    await sync_user_avatars(db, uid)

    fresh = await db.users.find_one(
        {"id": uid},
        {"_id": 0},
    )

    if fresh:
        u = fresh

    metrics = await compute_user_metrics(db, uid)

    achievement_count = await db.user_achievements.count_documents({
        "user_id": uid,
    })

    followers_count = len(u.get("followers", []))
    following_count = len(u.get("following", []))
    featured_achievement = None

    earned = await db.user_achievements.find_one(
        {"user_id": uid},
        {"_id": 0},
        sort=[("earned_at", -1)],
    )

    achievement_id = None

    if earned:
        achievement_id = earned.get("achievement_id")

    achievement_def = next(
        (
            item
            for item in ACHIEVEMENT_DEFS
            if item["id"] == achievement_id
        ),
        None,
    )

    if achievement_def:
        featured_achievement = {
            **achievement_def,
            "earned_at": earned.get("earned_at"),
        }

    owned_avatars = u.get(
        "owned_avatars",
        DEFAULT_AVATARS.copy(),
    )

    return {
        "id": u["id"],
        "username": u.get("username", ""),
        "display_name": u.get("display_name") or u.get("name", ""),
        "bio": u.get("bio", ""),
        "is_public": u.get("is_public", True),
        "selected_theme": u.get("selected_theme", "light"),
        "level_data": xp_progress(u.get("xp", 0)),
        "coin_balance": u.get("coin_balance", 0),
        "streak_days": metrics.get("current_max_streak", 0),
        "achievement_count": achievement_count,
        "featured_achievement": featured_achievement,
        "avatar": u.get("avatar", "explorer"),
        "owned_avatars": owned_avatars,
        "followers_count": followers_count,
        "following_count": following_count,
        "following": u.get("following", []),
        "followers": u.get("followers", []),
        "avatar_store": list(AVATAR_STORE.values()),
        "created_at": u.get("created_at"),
    }


async def cleanup_expired_password_resets():
    while True:
        try:
            await db.password_resets.delete_many({
                "expires_at": {
                    "$lt": now_utc_iso(),
                }
            })
        except Exception:
            logger.exception("Failed to clean up expired password reset tokens")

        await asyncio.sleep(60 * 60 * 24)

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

    if int(payload.get("ver", 0)) != int(user.get("token_version", 0)):
        raise HTTPException(status_code=401, detail="Session revoked")

    return user

async def create_friend_activity_notifications(
    actor_user_id: str,
    activity_type: str,
    message: str,
    metadata: dict | None = None,
):
    if not actor_user_id:
        return

    actor = await db.users.find_one(
        {"id": actor_user_id},
        {"followers": 1, "_id": 0},
    )

    if not actor:
        return

    followers = actor.get("followers", [])

    if not followers:
        return

    now = datetime.now(timezone.utc).isoformat()

    notifications = []

    for recipient_id in followers:
        if not recipient_id or recipient_id == actor_user_id:
            continue

        notifications.append(
            {
                "id": str(uuid.uuid4()),
                "user_id": recipient_id,
                "actor_user_id": actor_user_id,
                "type": activity_type,
                "message": message,
                "metadata": metadata or {},
                "read": False,
                "created_at": now,
            }
        )

    if notifications:
        await db.notifications.insert_many(notifications)

        # Send push notifications
        for recipient_id in followers:
            if not recipient_id or recipient_id == actor_user_id:
                continue

            await send_push_notification(
                user_id=recipient_id,
                title="Friend Activity",
                body=message,
                data={
                    "type": activity_type,
                    "actor_user_id": actor_user_id,
                },
            )


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


# ============== Models ==============

class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: Optional[str] = Field(default="", max_length=80)

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class GoogleAuthIn(BaseModel):
    id_token: str


class AppleAuthIn(BaseModel):
    identity_token: str
    name: Optional[str] = ""

class HabitIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default="", max_length=300)
    frequency: Literal["daily", "weekly"] = "daily"
    difficulty: Optional[Literal["easy", "medium", "hard"]] = "medium"
    custom_coins: Optional[int] = Field(default=None, ge=1, le=100)
    icon: Optional[str] = Field(default="flame", max_length=40)
    category: Optional[str] = Field(default=None, max_length=60)
    reminder_enabled: Optional[bool] = False
    reminder_time: Optional[str] = Field(default=None, max_length=5)


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

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
    
class ProfileUpdateIn(BaseModel):
    username: Optional[str] = None
    display_name: Optional[str] = None
    bio: Optional[str] = None
    is_public: Optional[bool] = None
    avatar: Optional[str] = None

class ActivityReactionIn(BaseModel):
    reaction: Literal["like", "cheer"]

class PushTokenIn(BaseModel):
    token: str
    platform: Optional[str] = None

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
        "auth_providers": ["password"],
        "google_id": None,
        "apple_id": None,
        "id": user_id,
        "email": email,
        "password_hash": hash_password(body.password),
        "name": body.name or email.split("@")[0],
        "display_name": body.name or "",
        "bio": "",
        "is_public": True,
        "followers": [],
        "following": [],
        "coin_balance": 0,
        "xp": 0,
        "avatar": "explorer",
        "owned_avatars": DEFAULT_AVATARS.copy(),
        "selected_theme": "light",
        "owned_themes": DEFAULT_THEMES.copy(),
        "onboarding_completed": False,
        "created_at": now_utc_iso(),
        "token_version": 0,
    }

    await db.users.insert_one(user_doc)

    token = create_access_token(user_id, email, 0)

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
    
    if (
    not user
    or not user.get("password_hash")
    or not verify_password(body.password, user["password_hash"])
    ):
        logger.warning(f"Failed login attempt for {email}")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(
        user["id"],
        email,
        int(user.get("token_version", 0)),
    )

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

@api_router.post("/auth/google")
@limiter.limit("10/minute")
async def google_auth(
    request: Request,
    body: GoogleAuthIn,
    response: Response,
):
    try:
        payload = id_token.verify_oauth2_token(
            body.id_token,
            google_requests.Request(),
        )

    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid Google token",
        )

    audience = payload.get("aud")

    allowed_audiences = [
        aud for aud in (
            GOOGLE_WEB_CLIENT_ID,
            GOOGLE_IOS_CLIENT_ID,
            GOOGLE_ANDROID_CLIENT_ID,
        )
        if aud
    ]

    if not allowed_audiences or audience not in allowed_audiences:
        raise HTTPException(
            status_code=401,
            detail="Invalid Google client",
        )

    google_id = payload.get("sub")
    email = payload.get("email", "").lower()

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Google account missing email",
        )

    user = await db.users.find_one(
    {
        "$or": [
            {"google_id": google_id},
            {"email": email},
        ]
    },
    {"_id": 0},
    )

    if not user:
        user = {
            "id": str(uuid.uuid4()),
            "email": email,
            "password_hash": None,
            "google_id": google_id,
            "followers": [],
            "following": [],
            "auth_providers": ["google"],
            "name": payload.get("name", email.split("@")[0]),
            "display_name": payload.get("name", ""),
            "bio": "",
            "is_public": True,
            "coin_balance": 0,
            "xp": 0,
            "avatar": "explorer",
            "owned_avatars": DEFAULT_AVATARS.copy(),
            "selected_theme": "light",
            "owned_themes": DEFAULT_THEMES.copy(),
            "created_at": now_utc_iso(),
            "token_version": 0,
        }

        await db.users.insert_one(user)

    else:
        updates = {}

        providers = user.get("auth_providers", [])

        if "google" not in providers:
            providers.append("google")
            updates["auth_providers"] = providers

        if not user.get("google_id"):
            updates["google_id"] = google_id

        if updates:
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": updates},
            )

            user.update(updates)

    return build_auth_response(user, response)

@api_router.post("/auth/unlink-google")
@limiter.limit("10/minute")
async def unlink_google(
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
):
    fresh_user = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not fresh_user:
        raise HTTPException(status_code=404, detail="User not found")

    providers = fresh_user.get("auth_providers", ["password"])
    is_linked = "google" in providers or bool(fresh_user.get("google_id"))

    # Idempotent: nothing to unlink.
    if not is_linked:
        return build_auth_response(fresh_user, response)

    # Safety guard: never leave a user without a usable login method.
    if not fresh_user.get("password_hash"):
        raise HTTPException(
            status_code=400,
            detail="Set a password before unlinking Google, otherwise you'll lose access to your account.",
        )

    new_providers = [p for p in providers if p != "google"]
    if "password" not in new_providers:
        new_providers.append("password")

    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"google_id": None, "auth_providers": new_providers}},
    )
    fresh_user["google_id"] = None
    fresh_user["auth_providers"] = new_providers
    return build_auth_response(fresh_user, response)

@api_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


@api_router.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return clean_user(user)

@api_router.get("/feed")
async def get_social_feed(
    user: dict = Depends(get_current_user),
):
    following_ids = user.get("following", [])

    feed_user_ids = list(
        set(following_ids + [user["id"]])
    )

    items = await db.activity_feed.find(
        {
            "user_id": {
                "$in": feed_user_ids,
            }
        },
        {"_id": 0},
    ).sort("created_at", -1).to_list(100)

    items = await attach_reaction_summaries(
    items,
    user["id"],
    )

    return {
    "items": items,
    }

@api_router.post("/activity/{activity_id}/react")
async def react_to_activity(
    activity_id: str,
    body: ActivityReactionIn,
    user: dict = Depends(get_current_user),
):
    activity = await db.activity_feed.find_one(
        {"id": activity_id},
        {"_id": 0},
    )

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity not found",
        )

    reaction_doc = {
        "id": str(uuid.uuid4()),
        "activity_id": activity_id,
        "user_id": user["id"],
        "reaction": body.reaction,
        "created_at": now_utc_iso(),
    }

    result = await db.activity_reactions.update_one(
        {
            "activity_id": activity_id,
            "user_id": user["id"],
            "reaction": body.reaction,
        },
        {
            "$setOnInsert": reaction_doc,
        },
        upsert=True,
    )

    activity_owner_id = activity.get("user_id")

    if result.upserted_id and activity_owner_id and activity_owner_id != user["id"]:
        actor_name = (
            user.get("username")
            or user.get("display_name")
            or user.get("name")
            or "Someone"
        )

        reaction_label = {
            "like": "liked",
            "cheer": "cheered",
        }.get(body.reaction, "reacted to")

        message = f"{actor_name} {reaction_label} your activity"

        await create_notification(
            db=db,
            user_id=activity_owner_id,
            actor_id=user["id"],
            notification_type="activity_reaction",
            message=message,
            target_id=activity_id,
        )

        await send_push_notification(
            user_id=activity_owner_id,
            title="New Reaction",
            body=message,
            data={
                "type": "activity_reaction",
                "actor_user_id": user["id"],
                "activity_id": activity_id,
                "reaction": body.reaction,
            },
        )

    return {
        "ok": True,
        "reaction": body.reaction,
    }


@api_router.delete("/activity/{activity_id}/react/{reaction}")
async def remove_activity_reaction(
    activity_id: str,
    reaction: Literal["like", "cheer"],
    user: dict = Depends(get_current_user),
):
    await db.activity_reactions.delete_one(
        {
            "activity_id": activity_id,
            "user_id": user["id"],
            "reaction": reaction,
        }
    )

    return {
        "ok": True,
        "reaction": reaction,
    }

@api_router.get("/profile/me")
async def get_my_profile(user: dict = Depends(get_current_user)):
    return await clean_profile(user)


@api_router.put("/profile/me")
async def update_my_profile(
    body: ProfileUpdateIn,
    user: dict = Depends(get_current_user),
):
    updates = {}

    if body.username is not None:
        username = body.username.lower().strip()

        reserved_usernames = {
            "admin",
            "administrator",
            "support",
            "ourorbit",
            "system",
            "moderator",
            "staff",
        }

        if len(username) < 3:
            raise HTTPException(
                status_code=400,
                detail="Username must be at least 3 characters",
            )

        if len(username) > 24:
            raise HTTPException(
                status_code=400,
                detail="Username must be 24 characters or fewer",
            )

        if username in reserved_usernames:
            raise HTTPException(
                status_code=400,
                detail="Username is reserved",
            )

        if not username.replace("_", "").isalnum():
            raise HTTPException(
                status_code=400,
                detail="Username can only contain letters, numbers, and underscores",
            )

        existing = await db.users.find_one({
            "username": username,
            "id": {"$ne": user["id"]},
        })

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Username already taken",
            )

        updates["username"] = username

    if body.display_name is not None:
        updates["display_name"] = body.display_name.strip()

    if body.avatar is not None:
        if body.avatar not in user.get(
            "owned_avatars",
            DEFAULT_AVATARS.copy(),
        ):
            raise HTTPException(
                status_code=403,
                detail="Avatar not owned",
            )

        updates["avatar"] = body.avatar

    if body.bio is not None:
        updates["bio"] = body.bio.strip()

    if body.is_public is not None:
        updates["is_public"] = body.is_public

    if updates:
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": updates},
        )

    fresh = await db.users.find_one(
        {"id": user["id"]},
        {"_id": 0},
    )

    return await clean_profile(fresh)


@api_router.get("/profile/{username}")
async def get_public_profile(username: str):
    user = await db.users.find_one(
        {"username": username.lower()},
        {"_id": 0},
    )

    if not user:
        raise HTTPException(status_code=404, detail="Profile not found")

    if not user.get("is_public", True):
        raise HTTPException(status_code=403, detail="Profile is private")

    return await clean_profile(user)

@api_router.get("/users/search")
async def search_users(
    q: str,
):
    query = q.strip()

    if len(query) < 2:
        return []

    escaped_query = re.escape(query)

    users = await db.users.find(
        {
            "is_public": True,
            "username": {
                "$exists": True,
                "$ne": "",
            },
            "$or": [
                {
                    "username": {
                        "$regex": escaped_query,
                        "$options": "i",
                    }
                },
                {
                    "display_name": {
                        "$regex": escaped_query,
                        "$options": "i",
                    }
                },
                {
                    "name": {
                        "$regex": escaped_query,
                        "$options": "i",
                    }
                },
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "username": 1,
            "display_name": 1,
            "name": 1,
            "avatar": 1,
        },
    ).limit(20).to_list(20)

    return users

@api_router.get("/notifications")
async def get_notifications(current_user=Depends(get_current_user)):
    user_id = current_user["id"]

    notifications = (
        await db.notifications.find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(50)
        .to_list(length=50)
    )

    for notification in notifications:
        notification.pop("_id", None)

    return {
        "ok": True,
        "notifications": notifications,
    }


@api_router.get("/notifications/unread-count")
async def get_unread_notification_count(current_user=Depends(get_current_user)):
    user_id = current_user["id"]

    count = await db.notifications.count_documents(
        {
            "user_id": user_id,
            "read": False,
        }
    )

    return {
        "ok": True,
        "count": count,
    }


@api_router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user=Depends(get_current_user),
):
    user_id = current_user["id"]

    result = await db.notifications.update_one(
        {
            "id": notification_id,
            "user_id": user_id,
        },
        {
            "$set": {
                "read": True,
                "read_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"ok": True}


@api_router.post("/notifications/read-all")
async def mark_all_notifications_read(current_user=Depends(get_current_user)):
    user_id = current_user["id"]

    await db.notifications.update_many(
        {
            "user_id": user_id,
            "read": False,
        },
        {
            "$set": {
                "read": True,
                "read_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    return {"ok": True}

@api_router.post("/push/register")
async def register_push_token(
    body: PushTokenIn,
    user: dict = Depends(get_current_user),
):
    await db.push_tokens.update_one(
        {
            "user_id": user["id"],
            "token": body.token,
        },
        {
            "$set": {
                "user_id": user["id"],
                "token": body.token,
                "platform": body.platform,
                "updated_at": now_utc_iso(),
            },
            "$setOnInsert": {
                "id": str(uuid.uuid4()),
                "created_at": now_utc_iso(),
            },
        },
        upsert=True,
    )

    return {"ok": True}

@api_router.post("/users/{target_id}/follow")
async def follow_user(
    target_id: str,
    user: dict = Depends(get_current_user),
):

    if target_id == user["id"]:
        raise HTTPException(
            status_code=400,
            detail="Cannot follow yourself",
        )

    target = await db.users.find_one({"id": target_id})
    
    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if not target.get("is_public", True):
        raise HTTPException(
            status_code=403,
            detail="This profile is private",
        )

    following_before = user.get("following", [])

    print("FOLLOW DEBUG")
    print("actor user_id:", user["id"])
    print("target_id:", target_id)
    print("already following:", target_id in following_before)

    if target_id in following_before:
        return {"ok": True}
    
    await db.users.update_one(
    {"id": user["id"]},
    {
        "$addToSet": {
            "following": target_id,
        }
    },
)
    await db.users.update_one(
    {"id": target_id},
    {
        "$addToSet": {
            "followers": user["id"],
        }
    },
)

    actor_name = (
    user.get("username")
    or user.get("display_name")
    or user.get("name")
    or "Someone"
)

    await create_notification(
        db=db,
        user_id=target_id,
        actor_id=user["id"],
        notification_type="followed_you",
        message=f"{actor_name} followed you",
        target_id=user["id"],
    )
    await send_push_notification(
        user_id=target_id,
        title="New Follower",
        body=f"{actor_name} followed you",
        data={
            "type": "followed_you",
            "actor_user_id": user["id"],
        },
    )

    await create_activity(
        user["id"],
        "follow_user",
        target_user_id=target_id,
        target_username=target.get("username", ""),
        target_display_name=target.get("display_name") or target.get("name", ""),
    )

    return {"ok": True}


@api_router.post("/users/{target_id}/unfollow")
async def unfollow_user(
    target_id: str,
    user: dict = Depends(get_current_user),
):
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$pull": {
                "following": target_id,
            }
        },
    )

    await db.users.update_one(
        {"id": target_id},
        {
            "$pull": {
                "followers": user["id"],
            }
        },
    )

    return {"ok": True}


@api_router.get("/users/{target_id}/followers")
async def get_followers(
    target_id: str,
    user: dict = Depends(get_current_user),
):
    target = await db.users.find_one({"id": target_id})

    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if (
    not target.get("is_public", True)
    and target["id"] != user["id"]
    ):
        raise HTTPException(
        status_code=403,
        detail="Profile is private",
    )

    follower_ids = target.get("followers", [])

    followers = await db.users.find(
        {
            "id": {
                "$in": follower_ids,
            }
        },
        {
            "_id": 0,
            "id": 1,
            "username": 1,
            "display_name": 1,
            "name": 1,
            "avatar": 1,
        },
    ).sort("username", 1).to_list(500)

    return {
        "count": len(followers),
        "followers": followers,
    }


@api_router.get("/users/{target_id}/following")
async def get_following(
    target_id: str,
    user: dict = Depends(get_current_user),
):
    target = await db.users.find_one({"id": target_id})

    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if (
        not target.get("is_public", True)
        and target["id"] != user["id"]
    ):
        raise HTTPException(
            status_code=403,
            detail="Profile is private",
        )

    following_ids = target.get("following", [])

    following = await db.users.find(
        {
            "id": {
                "$in": following_ids,
            }
        },
        {
            "_id": 0,
            "id": 1,
            "username": 1,
            "display_name": 1,
            "name": 1,
            "avatar": 1,
        },
    ).sort("username", 1).to_list(500)

    return {
        "count": len(following),
        "following": following,
    }


@api_router.get("/users/{target_id}/activity")
async def get_public_activity(
    target_id: str,
    user: dict = Depends(get_current_user),
    ):
    target = await db.users.find_one(
        {"id": target_id},
        {"_id": 0},
    )

    if not target:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if not target.get("is_public", True):
        raise HTTPException(
            status_code=403,
            detail="Profile is private",
        )

    items = await db.activity_feed.find(
        {"user_id": target_id},
        {"_id": 0},
    ).sort("created_at", -1).to_list(50)

    items = await attach_reaction_summaries(
    items,
    user["id"],
    )

    return {"items": items}


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
        {
            "$set": {"password_hash": new_hash},
            "$inc": {"token_version": 1},
        },
    )

    return {"ok": True, "message": "Password changed successfully"}
@api_router.post("/auth/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
):
    email = payload.email.lower()

    user = await db.users.find_one({"email": email})

    # Always return success to avoid email enumeration
    if not user:
        return {
            "ok": True,
            "message": "If an account exists, a reset link has been sent.",
        }

    # Clean up expired tokens
    await db.password_resets.delete_many({
        "expires_at": {
            "$lt": now_utc_iso(),
        }
    })

    # Invalidate existing active reset tokens
    await db.password_resets.update_many(
        {
            "user_id": user["id"],
            "used": False,
        },
        {
            "$set": {
                "used": True,
                "invalidated_at": now_utc_iso(),
            }
        },
    )

    reset_token = create_reset_token()

    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()

    await db.password_resets.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "email": email,
        "token": reset_token,
        "expires_at": expires_at,
        "used": False,
        "created_at": now_utc_iso(),
    })

    APP_URL = os.getenv(
        "APP_URL",
        "https://ourorbit.net",
    )

    reset_link = (
        f"{APP_URL}/reset-password?token={reset_token}"
    )

    send_password_reset_email(
        email,
        reset_link,
    )

    return {
        "ok": True,
        "message": "If an account exists, a reset link has been sent.",
    }
@api_router.post("/auth/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
):
    reset_doc = await db.password_resets.find_one({
        "token": payload.token,
        "used": False,
    })

    if not reset_doc:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired reset token",
        )

    try:
        expires_at = datetime.fromisoformat(
            reset_doc["expires_at"]
        )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid reset token",
        )

    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="Reset token expired",
        )

    new_hash = hash_password(payload.new_password)

    await db.users.update_one(
        {"id": reset_doc["user_id"]},
        {
            "$set": {
                "password_hash": new_hash,
            },
            "$inc": {"token_version": 1},
        },
    )

    await db.password_resets.update_many(
        {
            "user_id": reset_doc["user_id"],
            "used": False,
        },
        {
            "$set": {
                "used": True,
                "invalidated_at": now_utc_iso(),
            }
        },
    )

    return {
        "ok": True,
        "message": "Password reset successful",
    }

@api_router.delete("/auth/me")
async def delete_account(
    response: Response,
    user: dict = Depends(get_current_user),
):
    uid = user["id"]
    await db.password_resets.delete_many({"user_id": uid})
    await db.habits.delete_many({"user_id": uid})
    await db.tasks.delete_many({"user_id": uid})
    await db.rewards.delete_many({"user_id": uid})
    activity_ids = [
    a["id"]
    for a in await db.activity_feed.find(
        {"user_id": uid},
        {"id": 1, "_id": 0},
    ).to_list(None)
    ]

    await db.activity_reactions.delete_many({
    "activity_id": {"$in": activity_ids}
    })

    await db.activity_feed.delete_many({"user_id": uid})
    await db.redemptions.delete_many({"user_id": uid})
    await db.transactions.delete_many({"user_id": uid})
    await db.user_achievements.delete_many({"user_id": uid})
    await db.quest_claims.delete_many({"user_id": uid})
  
    await db.users.update_many(
    {},
    {
        "$pull": {
            "followers": uid,
            "following": uid,
        }
    },
    )    
    await db.activity_reactions.delete_many({"user_id": uid})

    await db.notifications.delete_many({
    "$or": [
        {"user_id": uid},
        {"actor_id": uid},
        {"actor_user_id": uid},
    ]
    })
   
    await db.users.delete_one({"id": uid})
    response.delete_cookie("access_token", path="/")

    return {"ok": True, "message": "Account deleted successfully"}
@api_router.post("/account/reset-data")
async def reset_account_data(
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
    activity_ids = [
    a["id"]
    for a in await db.activity_feed.find(
        {"user_id": uid},
        {"id": 1, "_id": 0},
    ).to_list(None)
    ]

    await db.activity_reactions.delete_many({
    "activity_id": {"$in": activity_ids}
    })

    await db.activity_feed.delete_many({"user_id": uid})
    await db.activity_reactions.delete_many({"user_id": uid})
    await db.notifications.delete_many({
        "$or": [
             {"user_id": uid},
             {"actor_id": uid},
             {"actor_user_id": uid},
        ]
    })
    await db.users.update_one(
        {"id": uid},
        {
            "$set": {
                "coin_balance": 0,
                "xp": 0,
                "selected_theme": "light",
                "owned_themes": DEFAULT_THEMES.copy(),
                "avatar": "explorer",
                "owned_avatars": DEFAULT_AVATARS.copy(),
}
        },
    )

    return {
        "ok": True,
        "message": "Account data reset successfully",
    }

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
        "reminder_enabled": bool(body.reminder_enabled),
        "reminder_time": body.reminder_time,
        "last_reminder_sent_date": None,
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

    await sync_user_achievements(db, user["id"])

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

        # NEW
        "reminder_enabled": bool(body.reminder_enabled),
        "reminder_time": body.reminder_time,
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

    STREAK_MILESTONES = [7, 14, 30, 100, 365]

    updated_habit = await db.habits.find_one_and_update(
        {
            "id": habit_id,
            "user_id": user["id"],
            "completions": {"$ne": today},
        },
        {
            "$addToSet": {"completions": today},
            "$set": {
                "last_completed_date": today,
                "streak": new_streak,
                "longest_streak": longest,
            },
            "$inc": {"total_completions": 1},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not updated_habit:
        raise HTTPException(status_code=400, detail="Already completed today")

    if new_streak in STREAK_MILESTONES:
        await create_notification(
            db=db,
            user_id=user["id"],
            actor_id=None,
            notification_type="streak_milestone",
            message=f'{habit["name"]} reached a {new_streak}-day streak!',
            target_id=habit["id"],
        )

        await send_push_notification(
            user_id=user["id"],
            title="🔥 Streak Milestone!",
            body=f'{habit["name"]} reached a {new_streak}-day streak!',
            data={
                "type": "streak_milestone",
                "habit_id": habit["id"],
                "streak": new_streak,
            },
        )

        await create_activity(
            user["id"],
            "streak_milestone",
            habit_id=habit["id"],
            habit_name=habit["name"],
            streak=new_streak,
        )

        await create_friend_activity_notifications(
            actor_user_id=user["id"],
            activity_type="streak_milestone",
            message=f'{user.get("display_name") or user.get("name")} reached a {new_streak}-day streak on {habit["name"]}',
            metadata={
                "habit_id": habit["id"],
                "habit_name": habit["name"],
                "streak": new_streak,
            },
        )

    coins = base_coins + bonus
    xp_data = await award_user_xp(user, coins)

    if xp_data["leveled_up"]:
        await create_activity(
        user["id"],
        "level_up",
        level=xp_data["new_level"],
        old_level=xp_data["old_level"],
        )
        
        await create_friend_activity_notifications(
            actor_user_id=user["id"],
            activity_type="level_up",
            message=f'{user.get("display_name") or user.get("name")} reached level {xp_data["new_level"]}',
            metadata={
                "level": xp_data["new_level"],
            },
        )
        await create_notification(
            db=db,
            user_id=user["id"],
            actor_id=None,
            notification_type="level_up",
            message=f'You reached level {xp_data["new_level"]}!',
            target_id=str(xp_data["new_level"]),
        )

        await send_push_notification(
            user_id=user["id"],
            title="⭐ Level Up!",
            body=f'You reached level {xp_data["new_level"]}!',
            data={
                "type": "level_up",
                "level": xp_data["new_level"],
            },
        )
    fresh_user = await db.users.find_one_and_update(
        {"id": user["id"]},
        {"$inc": {"coin_balance": coins, "xp": xp_data["xp_earned"]}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    new_balance = fresh_user["coin_balance"]

    desc = f"Completed habit: {habit['name']}"
    if bonus > 0:
        desc += f" (+{bonus} streak bonus)"

    await log_transaction(user["id"], coins, "earn", "habit", habit_id, desc)
    await create_activity(
    user["id"],
    "habit_complete",
    habit_name=habit["name"],
    streak=new_streak,
    coins=coins,
    )

    await create_friend_activity_notifications(
    actor_user_id=user["id"],
    activity_type="habit_complete",
    message=f'{user.get("display_name") or user.get("name")} completed {habit["name"]}',
    metadata={
        "habit_id": habit_id,
        "habit_name": habit["name"],
        "streak": new_streak,
    },
)
    
    newly_earned = await sync_user_achievements(db, user["id"])
    await create_achievement_activities(
    user["id"],
    newly_earned,
    )
    new_avatars = await sync_user_avatars(db, user["id"])
    updated = updated_habit
    updated["completed_today"] = True
    return {
        "user": clean_user(fresh_user),
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
        "new_avatars": new_avatars,
        "new_achievements": newly_earned,
    }
async def create_next_recurring_task_if_needed(task: dict, user_id: str):
    recurrence = task.get("recurrence", "none")

    if recurrence not in ("daily", "weekly"):
        return None

    delta_days = 1 if recurrence == "daily" else 7

    next_due = (
        datetime.now(timezone.utc).date() + timedelta(days=delta_days)
    ).isoformat()

    next_task_id = str(uuid.uuid4())

    await db.tasks.insert_one({
        "id": next_task_id,
        "user_id": user_id,
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

    return next_task_id
async def generate_weekly_recap_for_user(
    uid: str,
):
    now = datetime.now(timezone.utc)

    week_start_dt = now - timedelta(days=7)

    week_start = week_start_dt.date().isoformat()
    week_end = now.date().isoformat()

    existing = await db.weekly_recaps.find_one(
        {
            "user_id": uid,
            "week_start": week_start,
        }
    )

    if existing:
        existing.pop("_id", None)
        return existing

    activities = await db.activity_feed.find(
        {
            "user_id": uid,
            "created_at": {
                "$gte": week_start_dt.isoformat(),
            },
        }
    ).to_list(None)

    transactions = await db.transactions.find(
        {
            "user_id": uid,
            "created_at": {
                "$gte": week_start_dt.isoformat(),
            },
        }
    ).to_list(None)

    habits_completed = len(
        [
            a
            for a in activities
            if a.get("type") == "habit_complete"
        ]
    )

    tasks_completed = len(
        [
            a
            for a in activities
            if a.get("type") == "task_complete"
        ]
    )

    quests_completed = len(
        [
            a
            for a in activities
            if a.get("type") == "quest_complete"
        ]
    )

    achievements_unlocked = len(
        [
            a
            for a in activities
            if a.get("type") == "achievement_unlock"
        ]
    )

    level_ups = len(
        [
            a
            for a in activities
            if a.get("type") == "level_up"
        ]
    )

    coins_earned = sum(
        max(t.get("amount", 0), 0)
        for t in transactions
    )

    coins_spent = abs(
        sum(
            min(t.get("amount", 0), 0)
            for t in transactions
        )
    )

    xp_earned = (
        habits_completed * 10
        + tasks_completed * 15
        + quests_completed * 25
    )

    recap = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "week_start": week_start,
        "week_end": week_end,
        "habits_completed": habits_completed,
        "tasks_completed": tasks_completed,
        "quests_completed": quests_completed,
        "achievements_unlocked": achievements_unlocked,
        "level_ups": level_ups,
        "coins_earned": coins_earned,
        "coins_spent": coins_spent,
        "xp_earned": xp_earned,
        "created_at": now.isoformat(),
    }

    await db.weekly_recaps.insert_one(recap)

    recap.pop("_id", None)

    return recap


from pydantic import BaseModel

class PushTokenIn(BaseModel):
    token: str


@api_router.post("/notifications/register-device")
async def register_push_device(
    body: PushTokenIn,
    current_user: dict = Depends(get_current_user),
):
    token = body.token.strip()

    if not token.startswith("ExponentPushToken[") and not token.startswith("ExpoPushToken["):
        raise HTTPException(status_code=400, detail="Invalid Expo push token")

    await db.users.update_one(
        {"id": current_user["id"]},
        {
            "$addToSet": {"push_tokens": token},
        },
    )

    return {"ok": True}

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

    await sync_user_achievements(db, user["id"])

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
    task = await db.tasks.find_one_and_update(
        {
            "id": task_id,
            "user_id": user["id"],
            "completed": {"$ne": True},
        },
        {"$set": {"completed": True, "completed_at": now_utc_iso()}},
        projection={"_id": 0},
        return_document=ReturnDocument.BEFORE,
    )

    if not task:
        exists = await db.tasks.find_one(
            {"id": task_id, "user_id": user["id"]},
            {"_id": 0, "completed": 1},
        )
        if exists:
            raise HTTPException(status_code=400, detail="Task already completed")
        raise HTTPException(status_code=404, detail="Task not found")

    coins = task.get("coins_reward") or coins_for(
        task.get("difficulty"),
        task.get("custom_coins"),
    )

    xp_data = await award_user_xp(user, coins)

    if xp_data["leveled_up"]:
        await create_activity(
            user["id"],
            "level_up",
            level=xp_data["new_level"],
            old_level=xp_data["old_level"],
        )
        await create_notification(
            db=db,
            user_id=user["id"],
            actor_id=None,
            notification_type="level_up",
            message=f'You reached level {xp_data["new_level"]}!',
        target_id=str(xp_data["new_level"]),
        )

        await send_push_notification(
            user_id=user["id"],
            title="⭐ Level Up!",
            body=f'You reached level {xp_data["new_level"]}!',
            data={
                "type": "level_up",
                "level": xp_data["new_level"],
            },
        )
        await create_friend_activity_notifications(
            actor_user_id=user["id"],
            activity_type="level_up",
            message=f'{user.get("display_name") or user.get("name")} reached level {xp_data["new_level"]}',
            metadata={
                "level": xp_data["new_level"],
            },
        )
    fresh_user = await db.users.find_one_and_update(
        {"id": user["id"]},
        {"$inc": {"coin_balance": coins, "xp": xp_data["xp_earned"]}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    new_balance = fresh_user["coin_balance"]

    await log_transaction(
        user["id"],
        coins,
        "earn",
        "task",
        task_id,
        f"Completed task: {task['name']}",
    )

    await create_activity(
    user["id"],
    "task_complete",
    task_name=task["name"],
    coins=coins,
    )

    await create_friend_activity_notifications(
    actor_user_id=user["id"],
    activity_type="task_complete",
    message=f'{user.get("display_name") or user.get("name")} completed {task["name"]}',
    metadata={
        "task_id": task_id,
        "task_name": task["name"],
    },
)
    
    next_task_id = await create_next_recurring_task_if_needed(
    task,
    user["id"],
)

    newly_earned = await sync_user_achievements(db, user["id"])
    await create_achievement_activities(
    user["id"],
    newly_earned,
    )
    new_avatars = await sync_user_avatars(db, user["id"])
    return {
        "user": clean_user(fresh_user),
        "coins_earned": coins,
        "new_balance": new_balance,
        "next_task_id": next_task_id,
        "xp_earned": xp_data["xp_earned"],
        "level_data": xp_data["level_data"],
        "leveled_up": xp_data["leveled_up"],
        "old_level": xp_data["old_level"],
        "new_level": xp_data["new_level"],
        "new_avatars": new_avatars,
        "new_achievements": newly_earned,
    }


@api_router.post("/tasks/{task_id}/uncomplete")
async def uncomplete_task(task_id: str, user: dict = Depends(get_current_user)):
    task = await db.tasks.find_one_and_update(
        {
            "id": task_id,
            "user_id": user["id"],
            "completed": True,
        },
        {"$set": {"completed": False, "completed_at": None}},
        projection={"_id": 0},
        return_document=ReturnDocument.BEFORE,
    )

    if not task:
        exists = await db.tasks.find_one(
            {"id": task_id, "user_id": user["id"]},
            {"_id": 0},
        )
        if exists:
            raise HTTPException(status_code=400, detail="Task not completed")
        raise HTTPException(status_code=404, detail="Task not found")

    coins = task.get("coins_reward") or coins_for(
        task.get("difficulty"),
        task.get("custom_coins"),
    )

    xp_loss = coins * XP_PER_COIN
    fresh_user = await db.users.find_one_and_update(
        {"id": user["id"]},
        [
            {
                "$set": {
                    "coin_balance": {
                        "$max": [0, {"$subtract": [{"$ifNull": ["$coin_balance", 0]}, coins]}]
                    },
                    "xp": {
                        "$max": [0, {"$subtract": [{"$ifNull": ["$xp", 0]}, xp_loss]}]
                    },
                }
            }
        ],
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    new_balance = fresh_user["coin_balance"]

    await log_transaction(
        user["id"],
        -coins,
        "spend",
        "task_undo",
        task_id,
        f"Un-completed task: {task['name']}",
    )

    return {"coins_refunded": -coins, "new_balance": new_balance}

@api_router.post("/weekly-recaps/generate")
async def generate_weekly_recap(
    current_user=Depends(get_current_user),
):
    recap = await generate_weekly_recap_for_user(
        current_user["id"]
    )

    return recap


@api_router.get("/weekly-recaps")
async def get_weekly_recaps(
    current_user=Depends(get_current_user),
):
    recaps = await db.weekly_recaps.find(
        {
            "user_id": current_user["id"]
        }
    ).sort(
        "week_start",
        -1,
    ).limit(
        12
    ).to_list(
        12
    )

    for recap in recaps:
        recap.pop("_id", None)

    return {
        "items": recaps,
    }

@api_router.post("/onboarding/complete")
async def complete_onboarding(
    user: dict = Depends(get_current_user),
):
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "onboarding_completed": True,
            }
        },
    )

    return {
        "ok": True,
        "onboarding_completed": True,
    }
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
    charged_user = await db.users.find_one_and_update(
        {"id": user["id"], "coin_balance": {"$gte": cost}},
        {"$inc": {"coin_balance": -cost}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not charged_user:
        current = await db.users.find_one(
            {"id": user["id"]},
            {"_id": 0, "coin_balance": 1},
        )
        balance = int((current or {}).get("coin_balance", 0))
        raise HTTPException(
            status_code=400,
            detail=f"Not enough coins. Need {cost - balance} more.",
        )

    new_balance = charged_user["coin_balance"]

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

    newly_earned = await sync_user_achievements(db, user["id"])
    await create_achievement_activities(
    user["id"],
    newly_earned,
    )
    new_avatars = await sync_user_avatars(db, user["id"])

    return {
        "redemption": redemption,
        "new_balance": new_balance,
        "new_achievements": newly_earned,
        "new_avatars": new_avatars,
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
    metrics = await compute_user_metrics(db, uid)
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




@api_router.get("/achievements")
async def list_achievements(user: dict = Depends(get_current_user)):
    uid = user["id"]
    newly_earned_ids = await sync_user_achievements(db, uid)
    metrics = await compute_user_metrics(db, uid)

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


@api_router.get("/quests")
async def list_quests(user: dict = Depends(get_current_user)):
    uid = user["id"]
    metrics = await compute_quest_metrics(db, uid)

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

    metrics = await compute_quest_metrics(db, uid)
    progress = int(metrics.get(quest["metric"], 0))

    if progress < int(quest["target"]):
        raise HTTPException(status_code=400, detail="Quest not completed yet")

    try:
        await db.quest_claims.insert_one({
            "user_id": uid,
            "quest_id": quest_id,
            "period_key": period_key,
            "claimed_at": now_utc_iso(),
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=400, detail="Already claimed for this period")

    reward = int(quest["reward"])
    xp_data = await award_user_xp(user, reward)
    fresh_user = await db.users.find_one_and_update(
        {"id": uid},
        {"$inc": {"coin_balance": reward, "xp": xp_data["xp_earned"]}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    new_balance = fresh_user["coin_balance"]

    if xp_data["leveled_up"]:
        await create_activity(
            uid,
            "level_up",
            level=xp_data["new_level"],
            old_level=xp_data["old_level"],
        )
        
        await create_notification(
            db=db,
            user_id=user["id"],
            actor_id=None,
            notification_type="level_up",
            message=f'You reached level {xp_data["new_level"]}!',
            target_id=str(xp_data["new_level"]),
        )

        await send_push_notification(
            user_id=user["id"],
            title="⭐ Level Up!",
            body=f'You reached level {xp_data["new_level"]}!',
            data={
                "type": "level_up",
                "level": xp_data["new_level"],
            },
        )
        await create_friend_activity_notifications(
            actor_user_id=user["id"],
            activity_type="level_up",
            message=f'{user.get("display_name") or user.get("name")} reached level {xp_data["new_level"]}',
            metadata={
            "level": xp_data["new_level"],
            },
        )

    await log_transaction(
        uid,
        reward,
        "earn",
        "quest",
        quest_id,
        f"Quest reward: {quest['name']}",
    )
    await create_activity(
    uid,
    "quest_complete",
    quest_id=quest["id"],
    quest_name=quest["name"],
    coins=reward,
    period=quest["period"],
    )

    await create_friend_activity_notifications(
    actor_user_id=uid,
    activity_type="quest_complete",
    message=f'{user.get("display_name") or user.get("name")} completed the quest {quest["name"]}',
    metadata={
        "quest_id": quest["id"],
        "quest_name": quest["name"],
    },
)

    newly_earned = await sync_user_achievements(db, uid)

    await create_achievement_activities(
    uid,
    newly_earned,
)

    new_avatars = await sync_user_avatars(db, uid)

    return {
        "coins_earned": reward,
        "new_balance": new_balance,
        "quest_id": quest_id,
        "xp_earned": xp_data["xp_earned"],
        "level_data": xp_data["level_data"],
        "leveled_up": xp_data["leveled_up"],
        "old_level": xp_data["old_level"],
        "new_level": xp_data["new_level"],
        "new_avatars": new_avatars,
        "new_achievements": newly_earned,
    }


# ============== Themes ==============

@api_router.get("/themes/me")
async def get_my_themes(user: dict = Depends(get_current_user)):
    uid = user["id"]

    await sync_user_achievements(db, uid)

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

@api_router.get("/activity")
async def get_activity_feed(
    user: dict = Depends(get_current_user),
):
    items = await db.activity_feed.find(
        {"user_id": user["id"]},
        {"_id": 0},
    ).sort("created_at", -1).to_list(100)

    items = await attach_reaction_summaries(
    items,
    user["id"],
    )

    return {
    "items": items,
    }

@api_router.post("/streak-reminders/send-due")
async def send_due_streak_reminders(request: Request):
    cron_secret = os.environ.get("CRON_SECRET")

    if not cron_secret:
        raise HTTPException(status_code=503, detail="Cron endpoint is not configured")

    provided = request.headers.get("X-Cron-Secret")

    if not secrets.compare_digest(provided or "", cron_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    today = today_str()
    now = datetime.now(timezone.utc)
    current_time = now.strftime("%H:%M")


    habits = await db.habits.find(
        {
            "reminder_enabled": True,
            "reminder_time": {"$lte": current_time},
            "streak": {"$gt": 0},
            "last_reminder_sent_date": {"$ne": today},
        },
        {"_id": 0},
    ).to_list(500)

    sent = 0

    for habit in habits:
        completions = habit.get("completions", [])

        if today in completions:
            continue

        user_id = habit["user_id"]
        habit_name = habit.get("name", "Your habit")
        streak = habit.get("streak", 0)

        message = (
            f"{habit_name} is at {streak} day"
            f"{'' if streak == 1 else 's'}. Complete it today to keep it going."
        )

        await create_notification(
            db=db,
            user_id=user_id,
            actor_id=None,
            notification_type="streak_reminder",
            message=message,
            target_id=habit["id"],
        )

        await send_push_notification(
            user_id=user_id,
            title="🔥 Don’t break your streak!",
            body=message,
            data={
                "type": "streak_reminder",
                "habit_id": habit["id"],
            },
        )

        await db.habits.update_one(
            {"id": habit["id"]},
            {
                "$set": {
                    "last_reminder_sent_date": today,
                    "last_reminder_sent_at": now_utc_iso(),
                }
            },
        )

        sent += 1

    return {
        "ok": True,
        "checked": len(habits),
        "sent": sent,
    }

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

    price = int(theme["price"])

    updated_user = await db.users.find_one_and_update(
        {
            "id": user["id"],
            "coin_balance": {"$gte": price},
            "owned_themes": {"$ne": theme_id},
        },
        {
            "$inc": {"coin_balance": -price},
            "$addToSet": {"owned_themes": theme_id},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not updated_user:
        fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0})
        if theme_id in (fresh or {}).get("owned_themes", []):
            raise HTTPException(status_code=400, detail="Theme already owned")
        balance = int((fresh or {}).get("coin_balance", 0))
        raise HTTPException(
            status_code=400,
            detail=f"Not enough coins. Need {price - balance} more.",
        )

    new_balance = updated_user["coin_balance"]
    updated_owned = updated_user.get("owned_themes", [])

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

logger = logging.getLogger(__name__)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.now(timezone.utc)

    try:
        response = await call_next(request)

        duration = (
            datetime.now(timezone.utc) - start
        ).total_seconds()

        logger.info(
            f"{request.method} {request.url.path} "
            f"{response.status_code} "
            f"{duration:.3f}s"
        )

        return response

    except Exception:
        duration = (
            datetime.now(timezone.utc) - start
        ).total_seconds()

        logger.exception(
            f"FAILED {request.method} {request.url.path} "
            f"{duration:.3f}s"
        )

        raise

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
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error")

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
# --- Register Router & CORS ---

if ENVIRONMENT == "production":
    ALLOWED_ORIGINS = [
       "https://habioapp.co",
    "https://www.habioapp.co",
    "https://ourorbit.app",
    "https://www.ourorbit.app",
    "https://ourorbit.net",
    "https://www.ourorbit.net",
    "https://main.dsrkbok7uhqk.amplifyapp.com",
]
else:
    ALLOWED_ORIGINS = [
                "https://habioapp.co",
        "https://www.habioapp.co",
        "https://ourorbit.net",
        "https://www.ourorbit.net",
        "https://ourorbit.app",
        "https://www.ourorbit.app",
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


# --- Startup / Shutdown ---

@app.on_event("startup")
async def on_startup():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("google_id", sparse=True)
    await db.users.create_index("apple_id", sparse=True)
    await db.users.create_index("username", unique=True, sparse=True)
    await db.users.create_index("display_name")
    await db.habits.create_index("user_id")
    await db.tasks.create_index("user_id")
    await db.rewards.create_index("user_id")
    await db.redemptions.create_index("user_id")
    await db.transactions.create_index("user_id")
    await db.habits.create_index([("user_id", 1), ("created_at", -1)])
    await db.habits.create_index([("user_id", 1), ("last_completed_date", -1)])
    await db.habits.create_index([("user_id", 1), ("total_completions", -1)])
    await db.activity_feed.create_index("user_id")
    await db.activity_feed.create_index([("user_id", 1), ("created_at", -1)])
    await db.activity_feed.create_index([("user_id", 1), ("type", 1)])
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
    await db.notifications.create_index("user_id")
    await db.notifications.create_index("actor_user_id")
    await db.notifications.create_index("created_at")
    await db.notifications.create_index(
    [("user_id", 1), ("created_at", -1)]
    )
    await db.notifications.create_index(
    [("user_id", 1), ("read", 1)]
    )
    await db.weekly_recaps.create_index(
    [("user_id", 1), ("week_start", -1)]
    )

    await db.weekly_recaps.create_index(
    [("user_id", 1), ("week_start", 1)],
    unique=True,
    )
    await db.user_achievements.create_index(
        [("user_id", 1), ("achievement_id", 1)],
        unique=True,
    )
    await db.quest_claims.create_index(
        [("user_id", 1), ("quest_id", 1), ("period_key", 1)],
        unique=True,
    )
    await db.password_resets.create_index(
    [("token", 1)],
    unique=True,
)

    await db.password_resets.create_index(
    [("expires_at", 1)],
)
    await db.users.update_many(
    {"followers": {"$exists": False}},
    {"$set": {"followers": []}},
)

    await db.users.update_many(
    {"following": {"$exists": False}},
    {"$set": {"following": []}},
)

    await db.users.update_many(
    {"username": {"$exists": False}},
    {"$unset": {"username": ""}},
)

    await db.users.update_many(
        {"display_name": {"$exists": False}},
        [{"$set": {"display_name": {"$ifNull": ["$name", ""]}}}],
    )

    await db.users.update_many(
        {"bio": {"$exists": False}},
        {"$set": {"bio": ""}},
    )

    await db.users.update_many(
        {"is_public": {"$exists": False}},
        {"$set": {"is_public": True}},
    )

    await db.users.update_many(
        {"avatar": {"$exists": False}},
        {"$set": {"avatar": "explorer"}},
    )

    await db.users.update_many(
        {"onboarding_completed": {"$exists": False}},
        {"$set": {"onboarding_completed": False}},
    )

    await db.users.update_many(
        {"token_version": {"$exists": False}},
        {"$set": {"token_version": 0}},
    )

    await db.push_tokens.create_index(
        [("user_id", 1), ("token", 1)],
        unique=True,
    )
    await db.users.update_many(
        {"owned_avatars": {"$exists": False}},
        {"$set": {"owned_avatars": DEFAULT_AVATARS.copy()}},
    )
    await db.habits.update_many(
    {"reminder_enabled": {"$exists": False}},
    {
        "$set": {
            "reminder_enabled": False,
            "reminder_time": None,
            "last_reminder_sent_date": None,
        }
    },
    )

    await db.push_tokens.create_index(
    [("user_id", 1)],
    )
    
    asyncio.create_task(cleanup_expired_password_resets())
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if admin_email and admin_password:
        admin_email = admin_email.lower()
        existing = await db.users.find_one({"email": admin_email})

    else:
        existing = True

    if not existing and admin_email and admin_password:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "followers": [],
            "following": [],
            "password_hash": hash_password(admin_password),
            "name": "Admin",
            "display_name": "Admin",
            "bio": "",
            "is_public": True,
            "avatar": "explorer",
            "owned_avatars": DEFAULT_AVATARS.copy(),
            "coin_balance": 0,
            "xp": 0,
            "selected_theme": "light",
            "owned_themes": DEFAULT_THEMES.copy(),
            "created_at": now_utc_iso(),
            "token_version": 0,
        })

        logger.info(f"Seeded admin user: {admin_email}")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()
