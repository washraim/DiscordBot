import discord
from config import OWNER_ID
from storage import staff_levels, get_settings

def is_owner(m): return m.id == OWNER_ID


def member_levels(m):
    levels = set(staff_levels(m.id))
    s = get_settings()
    role_ids = {r.id for r in getattr(m, "roles", [])}
    if s.get("curator_role") in role_ids:
        levels.add("curator")
    if s.get("responsible_role") in role_ids:
        levels.add("responsible")
    return levels


def is_curator(m):
    return is_owner(m) or "curator" in member_levels(m)


def is_responsible(m):
    return is_owner(m) or bool(member_levels(m) & {"curator", "responsible"})


def is_staff(m):
    if is_responsible(m):
        return True
    s = get_settings()
    ids = {s.get("builder_role"), s.get("tester_role")} - {None}
    return any(r.id in ids for r in getattr(m, "roles", []))


async def send_log(client, key, embed):
    ch_id = get_settings().get(key)
    if not ch_id:
        return False
    ch = client.get_channel(ch_id)
    if ch is None:
        return False
    try:
        await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return False
    return True
