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


async def send_log_msg(client, key, embed, view=None, file=None):
    ch_id = get_settings().get(key)
    if not ch_id:
        return None
    ch = client.get_channel(ch_id)
    if ch is None:
        return None
    try:
        return await ch.send(embed=embed, view=view, file=file,
                             allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return None


async def send_log(client, key, embed):
    return (await send_log_msg(client, key, embed)) is not None
