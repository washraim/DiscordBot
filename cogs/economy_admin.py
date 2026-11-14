import datetime
import io
import time
import discord
from discord.ext import commands
from discord import app_commands
from storage import (
    get_user, add_points, remove_points, add_salary, remove_salary,
    reset_salary, get_settings, set_setting, all_users, log_salary, salary_entries, archive_salary,
)
from config import PERMS_CURATOR
from helpers import is_curator, is_owner, send_log, send_log_msg

RED = 0x9B111E
GREEN = 0x2ECC71

CURRENCY = {"points": "баллов", "salary": "ЗП"}

ACTIONS = {
    "add_points":    {"icon": "➕", "title": "Выдать баллы",  "field": "points", "give": True,  "color": GREEN},
    "remove_points": {"icon": "➖", "title": "Забрать баллы", "field": "points", "give": False, "color": RED},
    "add_salary":    {"icon": "💸", "title": "Выдать ЗП",     "field": "salary", "give": True,  "color": GREEN},
    "remove_salary": {"icon": "🧾", "title": "Забрать ЗП",    "field": "salary", "give": False, "color": RED},
}


def num(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def build_panel(user) -> discord.Embed:
    users = all_users()
    total_points = sum(u.get("points", 0) for u in users.values())
    total_salary = sum(u.get("salary", 0) for u in users.values())

    e = discord.Embed(
        title="💰 Управление экономикой",
        description=(
            "Выберите действие, затем участника и сумму.\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
        ),
        color=RED,
        timestamp=discord.utils.utcnow(),
    )
    e.add_field(
        name="📊 В обороте",
        value=f"🪙 **Баллы:** `{num(total_points)}`\n💵 **ЗП:** `{num(total_salary)}`",
        inline=True)
    e.add_field(
        name="ℹ️ Правила",
        value=("• Выдача ЗП уменьшается штрафом за выговоры (до −50%)\n"
               "• Забрать больше, чем есть у участника, нельзя\n"
               "• Все операции пишутся в канал «Переводы»"),
        inline=True)
    e.set_footer(text=f"Открыл: {user.display_name}", icon_url=user.display_avatar.url)
    return e


async def log_op(client, action_key, curator, target, amount, reason, balance_after, note=None):
    cfg = ACTIONS[action_key]
    e = discord.Embed(title=f"{cfg['icon']} {cfg['title']}", color=cfg["color"], timestamp=discord.utils.utcnow())
    e.add_field(name="Участник", value=f"{target.mention}\n`{target.id}`", inline=True)
    e.add_field(name="Куратор", value=curator.mention, inline=True)
    e.add_field(name="Сумма", value=f"**{num(amount)}** {CURRENCY[cfg['field']]}", inline=True)
    if note:
        e.add_field(name="Примечание", value=note, inline=False)
    e.add_field(name="Баланс после", value=num(balance_after), inline=True)
    e.add_field(name="Причина", value=(reason or "не указана")[:500], inline=False)
    await send_log(client, "pay_log", e)


class CuratorView(discord.ui.View):
    async def interaction_check(self, i: discord.Interaction) -> bool:
        if not is_curator(i.user):
            await i.response.send_message("❌ Нет прав.", ephemeral=True)
            return False
        return True


class AmountModal(discord.ui.Modal):
    def __init__(self, action_key, target, panel):
        cfg = ACTIONS[action_key]
        super().__init__(title=f"{cfg['icon']} {cfg['title']}"[:45])
        self.action_key, self.target, self.panel = action_key, target, panel

        label = "Сумма"
        if not cfg["give"]:
            label = f"Сумма (есть: {num(get_user(target.id)[cfg['field']])})"[:45]
        self.amount = discord.ui.TextInput(label=label, max_length=10, placeholder="Например: 100")
        self.reason = discord.ui.TextInput(label="Причина (необязательно)", required=False, max_length=200)
        self.add_item(self.amount)
        self.add_item(self.reason)

    async def on_submit(self, i: discord.Interaction):
        if not is_curator(i.user):
            return await i.response.send_message("❌ Нет прав.", ephemeral=True)
        if ACTIONS[self.action_key]["give"] and self.target.id == i.user.id and not is_owner(i.user):
            return await i.response.send_message("❌ Нельзя выдавать самому себе.", ephemeral=True)
        try:
            amt = int(self.amount.value.replace(" ", ""))
        except ValueError:
            return await i.response.send_message("❌ Сумма должна быть числом.", ephemeral=True)
        if amt <= 0:
            return await i.response.send_message("❌ Сумма должна быть больше 0.", ephemeral=True)

        key, cfg, t = self.action_key, ACTIONS[self.action_key], self.target
        reason = self.reason.value.strip() or None
        note = None

        if key == "add_points":
            add_points(t.id, amt)
            text = f"✅ +{num(amt)} баллов → {t.mention}"
        elif key == "add_salary":
            pen = min(get_user(t.id)["warnings"] * 10, 50)
            gross = amt
            amt = int(amt * (100 - pen) / 100)
            add_salary(t.id, amt)
            log_salary(t.id, gross, amt, i.user.id, "pay")
            note = f"Штраф за выговоры: −{pen}%" if pen else None
            text = f"✅ +{num(amt)} ЗП → {t.mention}" + (f" (штраф −{pen}%)" if pen else "")
        elif key == "remove_points":
            if remove_points(t.id, amt) is None:
                have = get_user(t.id)["points"]
                return await i.response.send_message(
                    f"❌ У {t.mention} только **{num(have)}** баллов.", ephemeral=True)
            text = f"✅ −{num(amt)} баллов у {t.mention}"
        else:
            if remove_salary(t.id, amt) is None:
                have = get_user(t.id)["salary"]
                return await i.response.send_message(
                    f"❌ У {t.mention} только **{num(have)}** ЗП.", ephemeral=True)
            log_salary(t.id, amt, amt, i.user.id, "take")
            text = f"✅ −{num(amt)} ЗП у {t.mention}"

        after = get_user(t.id)[cfg["field"]]
        await i.response.send_message(f"{text}. Баланс: **{num(after)}**.", ephemeral=True)
        await log_op(i.client, key, i.user, t, amt, reason, after, note)
        await self.panel.refresh()


class TargetPick(CuratorView):
    def __init__(self, action_key, panel):
        super().__init__(timeout=60)
        self.action_key, self.panel = action_key, panel

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Выберите участника")
    async def pick(self, i: discord.Interaction, sel):
        target = sel.values[0]
        if target.bot:
            return await i.response.edit_message(content="❌ У ботов нет баланса.", view=None)
        if ACTIONS[self.action_key]["give"] and target.id == i.user.id and not is_owner(i.user):
            return await i.response.edit_message(content="❌ Нельзя выдавать самому себе.", view=None)
        await i.response.send_modal(AmountModal(self.action_key, target, self.panel))


CLOSE_BRANCHES = {"builder": "Строители", "tester": "Тестеры", "both": "Обе ветки"}


def close_targets(guild, choice):
    keys = ("builder", "tester") if choice == "both" else (choice,)
    s = get_settings()
    ids, found = [], False
    for key in keys:
        role = guild.get_role(s.get(f"{key}_role") or 0)
        if role is None:
            continue
        found = True
        for m in role.members:
            if not m.bot and m.id not in ids and get_user(m.id)["salary"] > 0:
                ids.append(m.id)
    return ids, found


class ResetBranchView(CuratorView):
    def __init__(self, panel):
        super().__init__(timeout=60)
        self.panel = panel

    @discord.ui.select(placeholder="Чью ЗП отметить выплаченной", options=[
        discord.SelectOption(label="Строители", value="builder", emoji="🏗️"),
        discord.SelectOption(label="Тестеры", value="tester", emoji="🧪"),
        discord.SelectOption(label="Обе ветки", value="both", emoji="👥"),
    ])
    async def pick(self, i: discord.Interaction, sel):
        choice = sel.values[0]
        targets, found = close_targets(i.guild, choice)
        if not found:
            return await i.response.edit_message(
                content="❌ Роль ветки не настроена или удалена (её задаёт владелец в `/admin`).", view=None)
        if not targets:
            return await i.response.edit_message(content="Ни у кого из выбранных нет ЗП.", view=None)
        total = sum(get_user(u)["salary"] for u in targets)
        await i.response.edit_message(
            content=(f"⚠️ Отметить ЗП выплаченной: **{len(targets)}** чел., всего **{num(total)}** ЗП ({CLOSE_BRANCHES[choice]}).\n"
                     "Бот сохранит архив и файл со списком, обнулит счётчики ЗП и начнёт новый период. "
                     "Это нельзя отменить."),
            view=ConfirmResetView(choice, self.panel))


class ConfirmResetView(CuratorView):
    def __init__(self, choice, panel):
        super().__init__(timeout=60)
        self.choice, self.panel = choice, panel

    @discord.ui.button(label="Да, выплачено", style=discord.ButtonStyle.danger)
    async def yes(self, i: discord.Interaction, b):
        targets, _ = close_targets(i.guild, self.choice)
        rows = sorted(((uid, get_user(uid)["salary"]) for uid in targets), key=lambda r: r[1], reverse=True)
        if not rows:
            return await i.response.edit_message(content="Ни у кого из выбранных нет ЗП.", view=None)
        total = sum(v for _, v in rows)
        await i.response.defer()

        lines = ["Ник;ID;ЗП"]
        for uid, amount in rows:
            m = i.guild.get_member(uid)
            name = (m.display_name if m else str(uid)).replace(";", ",")
            lines.append(f"{name};{uid};{amount}")
        lines.append(f"ИТОГО;;{total}")
        data = "\n".join(lines).encode("utf-8-sig")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d")

        archive_salary(i.user.id, CLOSE_BRANCHES[self.choice], rows, total)
        count = reset_salary([uid for uid, _ in rows])
        set_setting("salary_period_start", time.time())

        e = discord.Embed(title="✅ ЗП выплачена, месяц закрыт", color=GREEN, timestamp=discord.utils.utcnow())
        e.add_field(name="Куратор", value=i.user.mention, inline=True)
        e.add_field(name="Ветка", value=CLOSE_BRANCHES[self.choice], inline=True)
        e.add_field(name="Выплачено", value=f"**{num(total)}** ЗП ({count} чел.)", inline=True)
        e.add_field(name="Новый период", value=f"с {discord.utils.format_dt(discord.utils.utcnow(), 'f')}", inline=False)
        await send_log_msg(i.client, "pay_log", e, file=discord.File(io.BytesIO(data), filename=f"zp_{stamp}.csv"))

        await i.edit_original_response(
            content=f"✅ Готово: обнулено у {count} чел., выплачено {num(total)} ЗП. Начался новый период.", view=None)
        await i.followup.send("Копия списка выплат:", ephemeral=True,
                              file=discord.File(io.BytesIO(data), filename=f"zp_{stamp}.csv"))
        self.stop()
        await self.panel.refresh()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no(self, i: discord.Interaction, b):
        await i.response.edit_message(content="Отменено.", view=None)
        self.stop()


PERIODS = {"cycle": ("С закрытия месяца", None), "week": ("Неделя", 7), "month": ("Месяц", 30), "all": ("Всё время", None)}
BRANCHES = {"all": "Все", "builder": "Строители", "tester": "Тестеры"}


def build_payroll(guild: discord.Guild, period: str, branch: str) -> discord.Embed:
    label, days = PERIODS[period]
    if period == "cycle":
        since = get_settings().get("salary_period_start") or 0
    else:
        since = time.time() - days * 86400 if days else 0
    entries = salary_entries(since)

    note = None
    if branch != "all":
        role_id = get_settings().get(f"{branch}_role")
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            note = "Роль этой ветки не настроена (её задаёт владелец в `/admin`)."
            entries = []
        else:
            ids = {m.id for m in role.members}
            entries = [e for e in entries if e["uid"] in ids]

    agg = {}
    for en in entries:
        a = agg.setdefault(en["uid"], {"gross": 0, "net": 0, "taken": 0})
        if en["kind"] == "pay":
            a["gross"] += en["gross"]
            a["net"] += en["net"]
        elif en["kind"] == "cancel":
            a["gross"] -= en["gross"]
            a["net"] -= en["net"]
        else:
            a["taken"] += en["net"]
    rows = sorted(agg.items(), key=lambda kv: kv[1]["net"] - kv[1]["taken"], reverse=True)

    e = discord.Embed(title=f"📋 Ведомость ЗП — {label}", color=RED, timestamp=discord.utils.utcnow())
    head = f"Ветка: **{BRANCHES[branch]}**"
    if days:
        head += f" · с {discord.utils.format_dt(discord.utils.utcnow() - datetime.timedelta(days=days), 'd')}"
    elif period == "cycle" and since:
        head += f" · с {discord.utils.format_dt(datetime.datetime.fromtimestamp(since, datetime.timezone.utc), 'd')}"
    if note:
        e.description = head + "\n" + note
        return e

    if not rows:
        e.description = head + "\n\nНачислений за этот период нет."
    else:
        lines = []
        for n, (uid, a) in enumerate(rows[:20], start=1):
            total = a["net"] - a["taken"]
            line = f"`{n}.` <@{uid}> — **{num(total)}**"
            pen = a["gross"] - a["net"]
            extra = []
            if pen:
                extra.append(f"штраф −{num(pen)}")
            if a["taken"]:
                extra.append(f"изъято {num(a['taken'])}")
            if extra:
                line += " (" + ", ".join(extra) + ")"
            lines.append(line)
        if len(rows) > 20:
            lines.append(f"… и ещё {len(rows) - 20}")
        e.description = head + "\n\n" + "\n".join(lines)

        tot_net = sum(a["net"] for a in agg.values())
        tot_pen = sum(a["gross"] - a["net"] for a in agg.values())
        tot_take = sum(a["taken"] for a in agg.values())
        e.add_field(name="Начислено", value=f"`{num(tot_net)}`", inline=True)
        e.add_field(name="Удержано штрафов", value=f"`{num(tot_pen)}`", inline=True)
        e.add_field(name="Изъято", value=f"`{num(tot_take)}`", inline=True)
    e.set_footer(text="Учёт ведётся с момента добавления ведомости")
    return e


class PeriodSelect(discord.ui.Select):
    def __init__(self, current):
        super().__init__(placeholder="Период", row=0, options=[
            discord.SelectOption(label=lbl, value=k, default=(k == current)) for k, (lbl, _) in PERIODS.items()])

    async def callback(self, i: discord.Interaction):
        v: PayrollView = self.view
        v.period = self.values[0]
        v.rebuild()
        await i.response.edit_message(embed=build_payroll(i.guild, v.period, v.branch), view=v)


class BranchSelect(discord.ui.Select):
    def __init__(self, current):
        super().__init__(placeholder="Ветка", row=1, options=[
            discord.SelectOption(label=lbl, value=k, default=(k == current)) for k, lbl in BRANCHES.items()])

    async def callback(self, i: discord.Interaction):
        v: PayrollView = self.view
        v.branch = self.values[0]
        v.rebuild()
        await i.response.edit_message(embed=build_payroll(i.guild, v.period, v.branch), view=v)


class PayrollView(CuratorView):
    def __init__(self):
        super().__init__(timeout=180)
        self.period, self.branch = "cycle", "all"
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        self.add_item(PeriodSelect(self.period))
        self.add_item(BranchSelect(self.branch))


class ActionButton(discord.ui.Button):
    def __init__(self, action_key, row):
        cfg = ACTIONS[action_key]
        super().__init__(label=cfg["title"], emoji=cfg["icon"], row=row,
                         style=discord.ButtonStyle.success if cfg["give"] else discord.ButtonStyle.danger)
        self.action_key = action_key

    async def callback(self, i: discord.Interaction):
        cfg = ACTIONS[self.action_key]
        await i.response.send_message(f"{cfg['icon']} **{cfg['title']}** — выберите участника:",
                                      view=TargetPick(self.action_key, self.view), ephemeral=True)


class EconomyView(CuratorView):
    def __init__(self, inter: discord.Interaction):
        super().__init__(timeout=600)
        self.inter = inter
        for key in ("add_points", "remove_points"):
            self.add_item(ActionButton(key, row=0))
        for key in ("add_salary", "remove_salary"):
            self.add_item(ActionButton(key, row=1))

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.inter.user.id:
            await i.response.send_message("❌ Это не ваша панель. Откройте свою через `/economy`.", ephemeral=True)
            return False
        return await super().interaction_check(i)

    async def refresh(self):
        try:
            await self.inter.edit_original_response(embed=build_panel(self.inter.user), view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Выплачено, обнулить ЗП", emoji="🧹", style=discord.ButtonStyle.secondary, row=2)
    async def reset_btn(self, i, b):
        await i.response.send_message("Выберите ветку:", view=ResetBranchView(self), ephemeral=True)

    @discord.ui.button(label="Ведомость ЗП", emoji="📋", style=discord.ButtonStyle.primary, row=2)
    async def payroll_btn(self, i: discord.Interaction, b):
        view = PayrollView()
        await i.response.send_message(embed=build_payroll(i.guild, view.period, view.branch), view=view, ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def reload(self, i: discord.Interaction, b):
        await i.response.edit_message(embed=build_panel(i.user), view=self)


class EconomyAdminCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="economy", description="Баллы и ЗП: выдача, изъятие, обнуление (куратор)")
    @app_commands.guild_only()
    @app_commands.default_permissions(**PERMS_CURATOR)
    async def economy_cmd(self, inter: discord.Interaction):
        if not is_curator(inter.user):
            return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        view = EconomyView(inter)
        await inter.response.send_message(embed=build_panel(inter.user), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(EconomyAdminCog(bot))
