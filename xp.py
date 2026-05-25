XP_PER_COIN = 2


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