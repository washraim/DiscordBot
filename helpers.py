import discord
from config import OWNER_ID
from storage import get_staff, get_settings

def is_owner(m): return m.id == OWNER_ID
def is_curator(m): return is_owner(m) or get_staff().get(str(m.id)) == "curator"
def is_responsible(m):
    if is_owner(m): return True
    return get_staff().get(str(m.id)) in ("curator", "responsible")


async def send_log(client, key, embed):
    """Отправить embed в канал логов, заданный в /admin под ключом key. Ошибки не пробрасываем."""
    ch_id = get_settings().get(key)
    if not ch_id:
        return
    ch = client.get_channel(ch_id)
    if ch is None:
        return
    try:
        await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass
