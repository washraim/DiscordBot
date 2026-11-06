import time
import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import PERMS_CURATOR, PERMS_RESPONSIBLE, WARN_EXPIRE_DAYS
from storage import (
    get_user, set_user, add_warn_record, warn_records, clear_warn_record, decay_warns,
)
from helpers import is_curator, is_responsible, is_owner, is_staff, send_log

RED = 0x9B111E
GREEN = 0x2ECC71


def _d(t):
    return f"<t:{int(t)}:d>"


def build_warnings_embed(member: discord.Member) -> discord.Embed:
    u = get_user(member.id)
    recs = warn_records(member.id)
    active = [w for w in recs if not w["cleared"]]
    legacy = max(0, u["warnings"] - len(active))

    e = discord.Embed(title=f"⚠️ Выговоры: {member.display_name}", color=RED)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Активных", value=str(u["warnings"]), inline=True)
    e.add_field(name="Штраф к ЗП", value=f"-{min(u['warnings'] * 10, 50)}%", inline=True)
    e.add_field(name="Автоснятие", value=f"1 выговор за {WARN_EXPIRE_DAYS} дн. без нарушений", inline=True)

    if active:
        lines = [f"`#{w['id']}` {_d(w['ts'])} · <@{w['by']}>\n└ {w['reason'][:150]}"
                 for w in sorted(active, key=lambda w: w["ts"])]
        e.add_field(name="📋 Активные", value="\n".join(lines)[:1024], inline=False)
    if legacy:
        e.add_field(name="Без записи", value=f"{legacy} шт. выдано до появления истории", inline=False)

    cleared = sorted((w for w in recs if w["cleared"]), key=lambda w: w["cleared"]["ts"], reverse=True)[:5]
    if cleared:
        lines = []
        for w in cleared:
            who = "автоматически" if w["cleared"]["by"] == "auto" else f"<@{w['cleared']['by']}>"
            lines.append(f"`#{w['id']}` {w['reason'][:60]} — снят {_d(w['cleared']['ts'])} ({who})")
        e.add_field(name="🕓 Снятые (последние 5)", value="\n".join(lines)[:1024], inline=False)
    if not recs and not u["warnings"]:
        e.description = "Выговоров нет. ✅"
    return e


def _clear_embed(curator, member, rec, left):
    e = discord.Embed(title="✅ Выговор снят", color=GREEN, timestamp=discord.utils.utcnow())
    e.add_field(name="Участник", value=member.mention, inline=True)
    e.add_field(name="Снял", value=curator.mention, inline=True)
    e.add_field(name="Осталось", value=str(left), inline=True)
    if rec:
        e.add_field(name="Выговор", value=f"#{rec['id']} · {rec['reason'][:300]}", inline=False)
    return e


class ModerationCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def cog_load(self):
        self.warn_decay.start()

    def cog_unload(self):
        self.warn_decay.cancel()

    @tasks.loop(hours=1)
    async def warn_decay(self):
        for uid, rec in decay_warns(WARN_EXPIRE_DAYS):
            left = get_user(uid)["warnings"]
            e = discord.Embed(title="⏳ Выговор снят автоматически", color=GREEN, timestamp=discord.utils.utcnow())
            e.add_field(name="Участник", value=f"<@{uid}>", inline=True)
            e.add_field(name="Осталось", value=str(left), inline=True)
            e.add_field(name="Выговор", value=f"#{rec['id']} · {rec['reason'][:300]}", inline=False)
            e.set_footer(text=f"{WARN_EXPIRE_DAYS} дн. без новых нарушений")
            await send_log(self.bot, "mod_log", e)

    @warn_decay.before_loop
    async def _before_decay(self): await self.bot.wait_until_ready()

    @app_commands.command(name="warn", description="Выговор")
    @app_commands.default_permissions(**PERMS_RESPONSIBLE)
    async def warn_cmd(self, inter: discord.Interaction, member: discord.Member):
        if not is_responsible(inter.user): return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        if member.bot: return await inter.response.send_message("❌ Нельзя выдать выговор боту.", ephemeral=True)
        if member.id == inter.user.id: return await inter.response.send_message("❌ Нельзя выдать выговор себе.", ephemeral=True)
        if is_owner(member): return await inter.response.send_message("❌ Нельзя выдать выговор владельцу.", ephemeral=True)
        await inter.response.send_modal(WarnModal(member))

    @app_commands.command(name="warnings", description="История выговоров участника")
    async def warnings_cmd(self, inter: discord.Interaction, member: discord.Member):
        if not is_staff(inter.user):
            return await inter.response.send_message("❌ Выговоры доступны только персоналу.", ephemeral=True)
        await inter.response.send_message(embed=build_warnings_embed(member), ephemeral=True)

    @app_commands.command(name="warn_clear", description="Снять выговор (куратор)")
    @app_commands.default_permissions(**PERMS_CURATOR)
    async def warn_clear_cmd(self, inter: discord.Interaction, member: discord.Member):
        if not is_curator(inter.user): return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        if member.id == inter.user.id:
            return await inter.response.send_message("❌ Нельзя снимать выговоры с самого себя.", ephemeral=True)
        u = get_user(member.id)
        if u["warnings"] <= 0: return await inter.response.send_message("Нет выговоров.", ephemeral=True)

        active = [w for w in warn_records(member.id) if not w["cleared"]]
        if not active:
            left = u["warnings"] - 1
            set_user(member.id, warnings=left)
            await inter.response.send_message(f"✅ Снят. Осталось: {left}.", ephemeral=True)
            return await send_log(inter.client, "mod_log", _clear_embed(inter.user, member, None, left))
        if len(active) == 1:
            return await self._do_clear(inter, member, active[0]["id"])
        await inter.response.send_message("Какой выговор снять?", view=ClearPick(self, member, active), ephemeral=True)

    async def _do_clear(self, inter: discord.Interaction, member, wid, edit=False):
        rec = clear_warn_record(member.id, wid, inter.user.id)
        if rec is None:
            text, left = "❌ Этот выговор уже снят.", None
        else:
            left = get_user(member.id)["warnings"]
            text = f"✅ Снят выговор #{rec['id']}. Осталось: {left}."
        if edit:
            await inter.response.edit_message(content=text, view=None)
        else:
            await inter.response.send_message(text, ephemeral=True)
        if rec:
            await send_log(inter.client, "mod_log", _clear_embed(inter.user, member, rec, left))


class ClearPick(discord.ui.View):
    def __init__(self, cog, member, active):
        super().__init__(timeout=60)
        self.cog, self.member = cog, member
        opts = [discord.SelectOption(label=f"#{w['id']} · {w['reason'][:70]}"[:100], value=str(w["id"]),
                                     description=time_str(w["ts"]))
                for w in sorted(active, key=lambda w: w["ts"])[:25]]
        sel = discord.ui.Select(placeholder="Выберите выговор", options=opts)
        sel.callback = self.picked
        self.sel = sel
        self.add_item(sel)

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if not is_curator(i.user):
            await i.response.send_message("❌ Нет прав.", ephemeral=True)
            return False
        return True

    async def picked(self, i: discord.Interaction):
        await self.cog._do_clear(i, self.member, int(self.sel.values[0]), edit=True)
        self.stop()


def time_str(t):
    return "Выдан " + time.strftime("%d.%m.%Y", time.localtime(t))


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))


class WarnModal(discord.ui.Modal, title="Выдать выговор"):
    w_reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, inter: discord.Interaction):
        wid = add_warn_record(self.member.id, inter.user.id, self.w_reason.value)
        u = get_user(self.member.id)
        e = discord.Embed(title="⚠️ Выговор", color=RED, timestamp=discord.utils.utcnow())
        e.add_field(name="Участник", value=self.member.mention, inline=True)
        e.add_field(name="Модератор", value=inter.user.mention, inline=True)
        e.add_field(name="Всего", value=str(u["warnings"]), inline=True)
        e.add_field(name="Причина", value=self.w_reason.value, inline=False)
        e.set_footer(text=f"Выговор #{wid} · автоснятие через {WARN_EXPIRE_DAYS} дн. без нарушений")
        await inter.response.send_message(embed=e, ephemeral=True)
        await send_log(inter.client, "mod_log", e)
