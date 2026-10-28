import discord
from discord.ext import commands
from discord import app_commands
from storage import (
    get_user, add_points, remove_points, add_salary, remove_salary,
    reset_salary, get_settings, all_users,
)
from helpers import is_curator, send_log

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
            amt = int(amt * (100 - pen) / 100)
            add_salary(t.id, amt)
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
        await i.response.send_modal(AmountModal(self.action_key, target, self.panel))


class ResetBranchView(CuratorView):
    def __init__(self, panel):
        super().__init__(timeout=60)
        self.panel = panel

    @discord.ui.select(placeholder="Ветка", options=[
        discord.SelectOption(label="Строители", value="builder", emoji="🏗️"),
        discord.SelectOption(label="Тестеры", value="tester", emoji="🧪"),
    ])
    async def pick(self, i: discord.Interaction, sel):
        role_id = get_settings().get(f"{sel.values[0]}_role")
        role = i.guild.get_role(role_id) if role_id else None
        if not role:
            return await i.response.edit_message(
                content="❌ Роль ветки не настроена или удалена (её задаёт владелец в `/admin`).", view=None)
        targets = [m.id for m in role.members if get_user(m.id)["salary"] > 0]
        if not targets:
            return await i.response.edit_message(content="Ни у кого из этой ветки нет ЗП.", view=None)
        total = sum(get_user(u)["salary"] for u in targets)
        await i.response.edit_message(
            content=(f"⚠️ Обнулить ЗП у **{len(targets)}** чел. (роль «{role.name}»), "
                     f"всего **{num(total)}** ЗП? Это нельзя отменить."),
            view=ConfirmResetView(targets, role.name, total, self.panel))


class ConfirmResetView(CuratorView):
    def __init__(self, targets, role_name, total, panel):
        super().__init__(timeout=60)
        self.targets, self.role_name, self.total, self.panel = targets, role_name, total, panel

    @discord.ui.button(label="Да, обнулить", style=discord.ButtonStyle.danger)
    async def yes(self, i: discord.Interaction, b):
        count = reset_salary(self.targets)
        await i.response.edit_message(content=f"✅ Обнулено у {count} чел.", view=None)
        self.stop()
        e = discord.Embed(title="🧹 Обнуление ЗП", color=RED, timestamp=discord.utils.utcnow())
        e.add_field(name="Куратор", value=i.user.mention, inline=True)
        e.add_field(name="Ветка", value=self.role_name, inline=True)
        e.add_field(name="Затронуто", value=f"{count} чел.", inline=True)
        e.add_field(name="Сумма", value=f"**{num(self.total)}** ЗП", inline=True)
        await send_log(i.client, "pay_log", e)
        await self.panel.refresh()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no(self, i: discord.Interaction, b):
        await i.response.edit_message(content="Отменено.", view=None)
        self.stop()


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

    @discord.ui.button(label="Обнулить ЗП ветки", emoji="🧹", style=discord.ButtonStyle.secondary, row=2)
    async def reset_btn(self, i, b):
        await i.response.send_message("Выберите ветку:", view=ResetBranchView(self), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def reload(self, i: discord.Interaction, b):
        await i.response.edit_message(embed=build_panel(i.user), view=self)


class EconomyAdminCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="economy", description="Баллы и ЗП: выдача, изъятие, обнуление (куратор)")
    @app_commands.guild_only()
    async def economy_cmd(self, inter: discord.Interaction):
        if not is_curator(inter.user):
            return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        view = EconomyView(inter)
        await inter.response.send_message(embed=build_panel(inter.user), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(EconomyAdminCog(bot))
