import asyncio
import discord
from discord.ext import commands
from helpers import send_log
from storage import get_settings

RED = 0x9B111E
ORANGE = 0xE67E22
GREEN = 0x2ECC71
BLUE = 0x3498DB
GREY = 0x95A5A6


def trim(text, n=1000):
    text = text or ""
    return text if len(text) <= n else text[:n - 1] + "…"


def now():
    return discord.utils.utcnow()


async def find_audit(guild: discord.Guild, action, target_id, within=15, channel_id=None):
    """Ищем свежую запись в журнале аудита, чтобы узнать модератора и причину."""
    me = guild.me
    if me is None or not me.guild_permissions.view_audit_log:
        return None
    try:
        async for entry in guild.audit_logs(limit=15, action=action):
            if getattr(entry.target, "id", None) != target_id:
                continue
            if (now() - entry.created_at).total_seconds() > within:
                continue
            if channel_id is not None:
                ch = getattr(getattr(entry, "extra", None), "channel", None)
                if ch is not None and ch.id != channel_id:
                    continue
            return entry
    except discord.HTTPException:
        return None
    return None


def mod_embed(title, color, target, entry, extra=None):
    e = discord.Embed(title=title, color=color, timestamp=now())
    e.add_field(name="Участник", value=f"{target.mention}\n`{target.id}`", inline=True)
    mod = entry.user.mention if entry and entry.user else "`не определён`"
    e.add_field(name="Модератор", value=mod, inline=True)
    for name, value in (extra or {}).items():
        e.add_field(name=name, value=value, inline=True)
    reason = trim(entry.reason, 500) if entry and entry.reason else "не указана"
    e.add_field(name="Причина", value=reason, inline=False)
    e.set_thumbnail(url=target.display_avatar.url)
    return e


class LogsCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    def _is_log_channel(self, channel_id):
        """Не логируем сами лог-каналы, иначе получится бесконечный цикл."""
        return channel_id in get_settings().values()

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not payload.guild_id or self._is_log_channel(payload.channel_id):
            return
        msg = payload.cached_message
        if msg is not None and msg.author.bot:
            return

        e = discord.Embed(title="🗑️ Сообщение удалено", color=RED, timestamp=now())
        e.add_field(name="Канал", value=f"<#{payload.channel_id}>", inline=True)
        if msg is not None:
            e.add_field(name="Автор", value=f"{msg.author.mention}\n`{msg.author.id}`", inline=True)
            e.add_field(name="Содержимое", value=trim(msg.content) or "*пусто*", inline=False)
            if msg.attachments:
                e.add_field(name="Вложения", value=trim("\n".join(a.filename for a in msg.attachments)), inline=False)
            e.set_thumbnail(url=msg.author.display_avatar.url)

            await asyncio.sleep(1)
            entry = await find_audit(msg.guild, discord.AuditLogAction.message_delete,
                                     msg.author.id, within=10, channel_id=payload.channel_id)
            if entry and entry.user and entry.user.id != msg.author.id:
                e.add_field(name="Удалил", value=entry.user.mention, inline=True)
        else:
            e.description = "Содержимое неизвестно: сообщение не сохранилось в кэше бота."
        e.set_footer(text=f"ID сообщения: {payload.message_id}")
        await send_log(self.bot, "message_log", e)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        if not payload.guild_id or self._is_log_channel(payload.channel_id):
            return
        e = discord.Embed(title="🧹 Массовое удаление сообщений", color=RED, timestamp=now())
        e.add_field(name="Канал", value=f"<#{payload.channel_id}>", inline=True)
        e.add_field(name="Удалено", value=str(len(payload.message_ids)), inline=True)
        await send_log(self.bot, "message_log", e)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if not payload.guild_id or self._is_log_channel(payload.channel_id):
            return
        after = payload.message
        if after is None or after.author.bot:
            return
        before = payload.cached_message
        if before is not None:
            if before.content == after.content:
                return 
        elif not payload.data.get("edited_timestamp"):
            return

        e = discord.Embed(title="✏️ Сообщение изменено", color=ORANGE, timestamp=now(),
                          url=after.jump_url)
        e.add_field(name="Канал", value=after.channel.mention, inline=True)
        e.add_field(name="Автор", value=f"{after.author.mention}\n`{after.author.id}`", inline=True)
        e.add_field(name="Было", value=trim(before.content) or "*пусто*" if before else "*неизвестно (нет в кэше)*",
                    inline=False)
        e.add_field(name="Стало", value=trim(after.content) or "*пусто*", inline=False)
        e.set_thumbnail(url=after.author.display_avatar.url)
        e.set_footer(text=f"ID сообщения: {after.id}")
        await send_log(self.bot, "message_log", e)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.bot:
            return

        if before.channel != after.channel:
            if before.channel is None:
                title, color = "🟢 Вход в голосовой канал", GREEN
                text = f"{member.mention} вошёл в {after.channel.mention}"
            elif after.channel is None:
                title, color = "🔴 Выход из голосового канала", RED
                text = f"{member.mention} вышел из {before.channel.mention}"
            else:
                title, color = "🔁 Переход между каналами", BLUE
                text = f"{member.mention}: {before.channel.mention} → {after.channel.mention}"
            e = discord.Embed(title=title, description=text, color=color, timestamp=now())
            e.set_thumbnail(url=member.display_avatar.url)
            e.set_footer(text=f"ID: {member.id}")
            await send_log(self.bot, "voice_log", e)

        for attr, on_title, off_title in (
            ("mute", "🔇 Серверный мут микрофона", "🔊 Серверный мут снят"),
            ("deaf", "🙉 Серверная глухота", "👂 Серверная глухота снята"),
        ):
            was, now_ = getattr(before, attr), getattr(after, attr)
            if was == now_:
                continue
            await asyncio.sleep(1)
            entry = await find_audit(member.guild, discord.AuditLogAction.member_update, member.id, within=10)
            e = mod_embed(on_title if now_ else off_title, RED if now_ else GREEN, member, entry)
            await send_log(self.bot, "mod_log", e)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await asyncio.sleep(1)
        entry = await find_audit(guild, discord.AuditLogAction.ban, user.id)
        await send_log(self.bot, "mod_log", mod_embed("🔨 Бан", RED, user, entry))

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await asyncio.sleep(1)
        entry = await find_audit(guild, discord.AuditLogAction.unban, user.id)
        await send_log(self.bot, "mod_log", mod_embed("♻️ Разбан", GREEN, user, entry))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await asyncio.sleep(1)
        entry = await find_audit(member.guild, discord.AuditLogAction.kick, member.id)
        if entry is None:
            return
        await send_log(self.bot, "mod_log", mod_embed("👢 Кик", ORANGE, member, entry))

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.timed_out_until == after.timed_out_until:
            return
        await asyncio.sleep(1)
        entry = await find_audit(after.guild, discord.AuditLogAction.member_update, after.id, within=10)
        until = after.timed_out_until
        if until and until > now():
            e = mod_embed("🔇 Мут (тайм-аут)", ORANGE, after, entry,
                          {"До": discord.utils.format_dt(until, "f") + f" ({discord.utils.format_dt(until, 'R')})"})
        else:
            e = mod_embed("🔊 Мут (тайм-аут) снят", GREEN, after, entry)
        await send_log(self.bot, "mod_log", e)


async def setup(bot):
    await bot.add_cog(LogsCog(bot))
