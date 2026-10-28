import discord
from discord.ext import commands
from discord import app_commands
from storage import all_users, get_user, transfer
from helpers import is_curator, is_responsible, is_owner, send_log
from storage import get_settings
from image_gen import XP_PER_LEVEL

RED = 0x9B111E
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

FEE_PERCENT = {"points": 0, "salary": 0}
CURRENCY_NAMES = {"points": "баллов", "salary": "ЗП"}


def num(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def _fmt_voice(u):  return f"{u['voice_time'] // 3600}ч {(u['voice_time'] % 3600) // 60}м"
def _fmt_msgs(u):   return num(u["messages"])
def _fmt_level(u):  return f"{u['xp'] // XP_PER_LEVEL + 1} LVL · {num(u['xp'])} XP"
def _fmt_points(u): return num(u["points"])
def _fmt_salary(u): return num(u["salary"])

CATS = {
    "voice":    ("🎙️", "Войс",       "voice_time", _fmt_voice),
    "messages": ("💬", "Сообщения",  "messages",   _fmt_msgs),
    "level":    ("⭐", "Уровень",    "xp",         _fmt_level),
    "points":   ("🪙", "Баллы",      "points",     _fmt_points),
    "salary":   ("💵", "ЗП",         "salary",     _fmt_salary),
}


def build_top_embed(guild: discord.Guild, viewer, cat: str) -> discord.Embed:
    emoji, name, field, fmt = CATS[cat]
    full = {"points": 0, "salary": 0, "warnings": 0, "messages": 0, "voice_time": 0, "xp": 0}

    rows = []
    for uid, data in all_users().items():
        u = {**full, **data}
        if u[field] <= 0:
            continue
        m = guild.get_member(int(uid))
        if m is None or m.bot:
            continue
        rows.append((m, u))
    rows.sort(key=lambda r: r[1][field], reverse=True)

    e = discord.Embed(title=f"🏆 Топ сервера — {emoji} {name}", color=RED, timestamp=discord.utils.utcnow())
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)

    if not rows:
        e.description = "Пока никого в рейтинге."
    else:
        lines = []
        for place, (m, u) in enumerate(rows[:10], start=1):
            badge = MEDALS.get(place, f"`{place}.`")
            lines.append(f"{badge} {m.mention} — **{fmt(u)}**")
        e.description = "\n".join(lines)

        for place, (m, u) in enumerate(rows, start=1):
            if m.id == viewer.id:
                e.set_footer(text=f"Ваше место: #{place} — {fmt(u)}")
                break
        else:
            e.set_footer(text="Вас пока нет в этом рейтинге")
    return e


class TopSelect(discord.ui.Select):
    def __init__(self, current: str):
        opts = [discord.SelectOption(label=name, value=key, emoji=emoji, default=(key == current))
                for key, (emoji, name, _, _) in CATS.items()]
        super().__init__(placeholder="Категория рейтинга", options=opts)

    async def callback(self, i: discord.Interaction):
        cat = self.values[0]
        for o in self.options:
            o.default = (o.value == cat)
        await i.response.edit_message(embed=build_top_embed(i.guild, i.user, cat), view=self.view)


class TopView(discord.ui.View):
    def __init__(self, inter: discord.Interaction, cat: str):
        super().__init__(timeout=120)
        self.inter = inter
        self.add_item(TopSelect(cat))

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.inter.user.id:
            await i.response.send_message("❌ Это меню открыл другой участник. Вызови `/top` сам.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass


def build_help_embed(user) -> discord.Embed:
    e = discord.Embed(
        title="📖 Справка по боту",
        description="Список команд и способы заработка.\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        color=RED,
    )
    e.add_field(
        name="👤 Для всех",
        value=(
            "`/profile` — карточка профиля\n"
            "`/balance` — баллы и ЗП\n"
            "`/top` — рейтинги сервера\n"
            "`/pay` — перевести баллы или ЗП\n"
            "`/shop` — магазин\n"
            "`/warnings` — выговоры участника\n"
            "`/help` — эта справка"
        ),
        inline=False,
    )
    e.add_field(
        name="💰 Как заработать",
        value=(
            "• **Сообщения:** 1 балл за каждые 5 сообщений, XP не чаще раза в 30 сек\n"
            "• **Войс:** 2 балла и XP за каждые 5 минут в голосовом канале\n"
            f"• **Уровень:** новый уровень каждые {num(XP_PER_LEVEL)} XP\n"
            "• **ЗП:** выдаёт куратор за работу, штраф за выговоры до −50%"
        ),
        inline=False,
    )
    if is_responsible(user):
        e.add_field(name="🛡️ Для ответственных", value="`/warn` — выдать выговор", inline=False)
    if is_curator(user):
        e.add_field(
            name="👑 Для кураторов",
            value=(
                "`/adm_shop` — управление магазином\n"
                "`/economy` — выдача и изъятие баллов и ЗП, обнуление ЗП\n"
                "`/warn_clear` — снять выговор (кроме себя)"
            ),
            inline=False,
        )
    if is_owner(user):
        e.add_field(
            name="🔑 Для владельца",
            value="`/admin` — каналы логов, роли веток, должности персонала",
            inline=False,
        )
    e.set_footer(text="Ashra Team • Вместе сильнее")
    return e


class ExtrasCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="help", description="Справка по командам бота")
    async def help_cmd(self, inter: discord.Interaction):
        await inter.response.send_message(embed=build_help_embed(inter.user), ephemeral=True)

    @app_commands.command(name="top", description="Рейтинги сервера")
    @app_commands.guild_only()
    @app_commands.describe(category="Что показывать (по умолчанию — войс)")
    @app_commands.choices(category=[
        app_commands.Choice(name=f"{emoji} {name}", value=key) for key, (emoji, name, _, _) in CATS.items()
    ])
    async def top_cmd(self, inter: discord.Interaction, category: app_commands.Choice[str] = None):
        cat = category.value if category else "voice"
        await inter.response.send_message(embed=build_top_embed(inter.guild, inter.user, cat),
                                          view=TopView(inter, cat))

    @app_commands.command(name="pay", description="Перевести баллы или ЗП участнику")
    @app_commands.guild_only()
    @app_commands.describe(currency="Что переводить", member="Кому перевести", amount="Сколько")
    @app_commands.choices(currency=[
        app_commands.Choice(name="Баллы", value="points"),
        app_commands.Choice(name="ЗП", value="salary"),
    ])
    async def pay_cmd(self, inter: discord.Interaction, currency: app_commands.Choice[str],
                      member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000_000]):
        if member.bot:
            return await inter.response.send_message("❌ Нельзя переводить боту.", ephemeral=True)
        if member.id == inter.user.id:
            return await inter.response.send_message("❌ Нельзя перевести самому себе.", ephemeral=True)

        field = currency.value
        fee = FEE_PERCENT[field]
        received = transfer(inter.user.id, member.id, field, amount, fee)
        if received is None:
            have = get_user(inter.user.id)[field]
            return await inter.response.send_message(
                f"❌ Недостаточно средств. У вас {num(have)} {CURRENCY_NAMES[field]}.", ephemeral=True)

        e = discord.Embed(title="💸 Перевод выполнен", color=RED, timestamp=discord.utils.utcnow())
        e.add_field(name="От", value=inter.user.mention, inline=True)
        e.add_field(name="Кому", value=member.mention, inline=True)
        e.add_field(name="Сумма", value=f"**{num(amount)}** {CURRENCY_NAMES[field]}", inline=True)
        if fee:
            e.add_field(name="Комиссия", value=f"{fee}% — получено {num(received)}", inline=False)
        e.set_thumbnail(url=member.display_avatar.url)
        await inter.response.send_message(content=member.mention, embed=e)

        if get_settings().get("pay_log") != inter.channel_id:
            log = discord.Embed(title="💸 Перевод", color=RED, timestamp=discord.utils.utcnow())
            log.add_field(name="От", value=f"{inter.user.mention}\n`{inter.user.id}`", inline=True)
            log.add_field(name="Кому", value=f"{member.mention}\n`{member.id}`", inline=True)
            log.add_field(name="Сумма", value=f"**{num(amount)}** {CURRENCY_NAMES[field]}", inline=True)
            if fee:
                log.add_field(name="Комиссия", value=f"{fee}% — получено {num(received)}", inline=False)
            await send_log(inter.client, "pay_log", log)


async def setup(bot):
    await bot.add_cog(ExtrasCog(bot))
