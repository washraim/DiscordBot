import discord
from discord.ext import commands
from discord import app_commands
from storage import get_user, add_salary, get_settings, reset_salary
from helpers import is_curator


class SalaryCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="salary_admin", description="Управление ЗП")
    async def salary_admin_cmd(self, inter: discord.Interaction):
        if not is_curator(inter.user): return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        e = discord.Embed(title="💵 Панель управления ЗП", description="Выдача и сброс.", color=0x9B111E)
        await inter.response.send_message(embed=e, view=SalaryAdminView(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(SalaryCog(bot))


class SalaryAdminView(discord.ui.View):
    def __init__(self): super().__init__(timeout=180)

    @discord.ui.button(label="💸 Выдать ЗП", style=discord.ButtonStyle.success)
    async def pay_btn(self, i, b): await i.response.send_message("Кому?", view=SalaryPayUserSelect(), ephemeral=True)

    @discord.ui.button(label="🧹 Обнулить", style=discord.ButtonStyle.danger)
    async def reset_btn(self, i, b): await i.response.send_message("Ветка:", view=ResetSalaryView(), ephemeral=True)


class SalaryPayUserSelect(discord.ui.View):
    def __init__(self): super().__init__(timeout=60)

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Выберите")
    async def pick(self, i, sel):
        if sel.values[0].bot:
            return await i.response.send_message("❌ Нельзя выдать ЗП боту.", ephemeral=True)
        await i.response.send_modal(SalaryPayModal(sel.values[0]))


class SalaryPayModal(discord.ui.Modal, title="Выдача ЗП"):
    amount = discord.ui.TextInput(label="Сумма", max_length=10)

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, i: discord.Interaction):
        try:
            amt = int(self.amount.value)
        except ValueError:
            return await i.response.send_message("❌ Нужно число.", ephemeral=True)
        if amt <= 0:
            return await i.response.send_message("❌ Сумма должна быть больше 0.", ephemeral=True)
        u = get_user(self.member.id)
        pen = min(u["warnings"] * 10, 50)
        final = int(amt * (100 - pen) / 100)
        add_salary(self.member.id, final)
        await i.response.send_message(f"✅ {self.member.mention} +{final} ЗП (штраф -{pen}%)", ephemeral=True)


class ResetSalaryView(discord.ui.View):
    def __init__(self): super().__init__(timeout=60)

    @discord.ui.select(placeholder="Ветка", options=[
        discord.SelectOption(label="Строители", value="builder"),
        discord.SelectOption(label="Тестеры", value="tester"),
    ])
    async def pick(self, i: discord.Interaction, sel):
        role_id = get_settings().get(f"{sel.values[0]}_role")
        role = i.guild.get_role(role_id) if role_id else None
        if not role:
            return await i.response.send_message("❌ Роль не настроена или была удалена.", ephemeral=True)
        targets = [m.id for m in role.members if get_user(m.id)["salary"] > 0]
        if not targets:
            return await i.response.edit_message(content="Ни у кого из этой ветки нет ЗП.", view=None)
        await i.response.edit_message(
            content=f"⚠️ Обнулить ЗП у **{len(targets)}** чел. (роль «{role.name}»)? Это нельзя отменить.",
            view=ConfirmResetView(targets),
        )


class ConfirmResetView(discord.ui.View):
    def __init__(self, targets):
        super().__init__(timeout=60)
        self.targets = targets

    @discord.ui.button(label="Да, обнулить", style=discord.ButtonStyle.danger)
    async def yes(self, i: discord.Interaction, b):
        count = reset_salary(self.targets)
        await i.response.edit_message(content=f"✅ Обнулено у {count} чел.", view=None)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no(self, i: discord.Interaction, b):
        await i.response.edit_message(content="Отменено.", view=None)
