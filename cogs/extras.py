import discord
from discord.ext import commands
from discord import app_commands
from storage import all_users, get_user, transfer
from helpers import is_curator, is_responsible, is_owner, is_staff, send_log
from storage import get_settings
from image_gen import XP_PER_LEVEL
from config import DAILY_VOICE_POINTS_LIMIT, DAILY_TEXT_POINTS_LIMIT, WARN_EXPIRE_DAYS

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


HELP_CATEGORIES = [
    ("main", "👤", "Основное", None),
    ("earn", "💰", "Заработок", None),
    ("shop", "🛒", "Магазин и заказы", None),
    ("staff", "🛠️", "Персонал", is_staff),
    ("mod", "🎀", "Ответственные", is_responsible),
    ("curator", "👑", "Кураторы", is_curator),
    ("owner", "🔑", "Владелец", is_owner),
]


def help_text(key, user):
    emoji, label = next((e, lb) for k, e, lb, _ in HELP_CATEGORIES if k == key)
    if key == "main":
        lines = [
            "`/profile` — Карточка профиля",
            "`/balance` — Баллы и ЗП",
            "`/top` — Рейтинги сервера",
            "`/pay` — Перевести баллы или ЗП участнику",
            "`/help` — Эта справка",
        ]
    elif key == "earn":
        lines = [
            f"**Сообщения** — 1 балл за каждые 5 сообщений, до {DAILY_TEXT_POINTS_LIMIT} баллов в сутки",
            f"**Войс** — 2 балла и XP за 5 минут с включённым микрофоном, до {DAILY_VOICE_POINTS_LIMIT} баллов в сутки",
            f"**Уровень** — новый уровень каждые {num(XP_PER_LEVEL)} XP",
            "**ЗП** — начисляют кураторы за работу, штраф за выговоры до −50%",
        ]
    elif key == "shop":
        lines = [
            "`/shop` — Магазин игроков или персонала (по вашей роли)",
            "`/orders` — Ваши заказы и их статусы",
            "О статусе заказа приходит уведомление в личные сообщения.",
        ]
    elif key == "staff":
        lines = [
            "`/warnings` — История выговоров участника",
            "**Выплаты** — в канале выплат своей ветки сообщение со строками `Выплата: 5000` и `Ник: ...` "
            "(или `Выплата 5000 Steve`) автоматически начисляет ЗП",
            "**Отчёт о ЗП** — 27-го числа бот присылает в канал ветки суммы заработанной ЗП",
        ]
    elif key == "mod":
        lines = ["`/warn` — Выдать выговор"]
        if is_curator(user):
            lines.append("`/warn_clear` — Снять выговор (кроме себя)")
        lines.append(f"Выговоры снимаются автоматически: 1 за {WARN_EXPIRE_DAYS} дн. без нарушений")
    elif key == "curator":
        lines = [
            "`/economy` — Баллы и ЗП: выдача, изъятие, обнуление, ведомость",
            "`/adm_shop` — Магазины: товары и лимиты",
            "`/salary_report` — Отчёт о заработанной ЗП по веткам",
            "`/activity` — Активность строителей и тестеров",
            "Заказы обрабатываются кнопками в канале покупок.",
        ]
    else:
        lines = ["`/admin` — Каналы логов и выплат, роли веток, должности персонала"]
    return f"### {emoji} {label}\n\n" + "\n".join(lines) + "\n\n-# Ashra Team • Вместе сильнее"


class HelpSelect(discord.ui.Select):
    def __init__(self, user, owner):
        options = [discord.SelectOption(label=label, value=key, emoji=emoji)
                   for key, emoji, label, check in HELP_CATEGORIES if check is None or check(user)]
        super().__init__(placeholder="Выберите категорию", options=options)
        self.owner = owner

    async def callback(self, i: discord.Interaction):
        self.owner.category = self.values[0]
        self.owner.build()
        await i.response.edit_message(view=self.owner)


class HelpView(discord.ui.LayoutView):
    def __init__(self, inter: discord.Interaction, category="main"):
        super().__init__(timeout=180)
        self.inter, self.category = inter, category
        self.select = None
        self.build()

    def build(self):
        self.clear_items()
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(help_text(self.category, self.inter.user)))
        row = discord.ui.ActionRow()
        self.select = HelpSelect(self.inter.user, self)
        row.add_item(self.select)
        container.add_item(row)
        self.add_item(container)

    async def on_timeout(self):
        if self.select is not None:
            self.select.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass


class ExtrasCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="help", description="Справка по командам бота")
    async def help_cmd(self, inter: discord.Interaction):
        await inter.response.send_message(view=HelpView(inter), ephemeral=True)

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
