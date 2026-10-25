import discord
from discord.ext import commands
from discord import app_commands
from storage import get_user, set_user, add_warning, get_settings
from helpers import is_curator, is_responsible, is_owner, send_log

RED = 0x9B111E
GREEN = 0x2ECC71


class ModerationCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="warn", description="Выговор")
    async def warn_cmd(self, inter: discord.Interaction, member: discord.Member):
        if not is_responsible(inter.user): return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        if member.bot: return await inter.response.send_message("❌ Нельзя выдать выговор боту.", ephemeral=True)
        if member.id == inter.user.id: return await inter.response.send_message("❌ Нельзя выдать выговор себе.", ephemeral=True)
        if is_owner(member): return await inter.response.send_message("❌ Нельзя выдать выговор владельцу.", ephemeral=True)
        await inter.response.send_modal(WarnModal(member))

    @app_commands.command(name="warnings", description="Выговоры участника")
    async def warnings_cmd(self, inter: discord.Interaction, member: discord.Member):
        u = get_user(member.id)
        e = discord.Embed(title=f"⚠️ Выговоры: {member.display_name}", color=RED)
        e.add_field(name="Всего", value=str(u["warnings"]), inline=True)
        e.add_field(name="Штраф к ЗП", value=f"-{min(u['warnings'] * 10, 50)}%", inline=True)
        await inter.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="warn_clear", description="Снять выговор (куратор)")
    async def warn_clear_cmd(self, inter: discord.Interaction, member: discord.Member):
        if not is_curator(inter.user): return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        if member.id == inter.user.id:
            return await inter.response.send_message("❌ Нельзя снимать выговоры с самого себя.", ephemeral=True)
        u = get_user(member.id)
        if u["warnings"] <= 0: return await inter.response.send_message("Нет выговоров.", ephemeral=True)
        left = u["warnings"] - 1
        set_user(member.id, warnings=left)

        e = discord.Embed(title="✅ Выговор снят", color=GREEN, timestamp=discord.utils.utcnow())
        e.add_field(name="Участник", value=member.mention, inline=True)
        e.add_field(name="Снял", value=inter.user.mention, inline=True)
        e.add_field(name="Осталось", value=str(left), inline=True)
        await inter.response.send_message(f"✅ Снят. Осталось: {left}.", ephemeral=True)
        await send_log(inter.client, "mod_log", e)


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))


class WarnModal(discord.ui.Modal, title="Выдать выговор"):
    w_reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, member):
        super().__init__()
        self.member = member

    async def on_submit(self, inter: discord.Interaction):
        add_warning(self.member.id)
        u = get_user(self.member.id)
        e = discord.Embed(title="⚠️ Выговор", color=RED, timestamp=discord.utils.utcnow())
        e.add_field(name="Участник", value=self.member.mention, inline=True)
        e.add_field(name="Модератор", value=inter.user.mention, inline=True)
        e.add_field(name="Всего", value=str(u["warnings"]), inline=True)
        e.add_field(name="Причина", value=self.w_reason.value, inline=False)
        await inter.response.send_message(embed=e)
        # В лог-канал, если он не совпадает с каналом, где вызвана команда (чтобы не дублировать)
        if get_settings().get("mod_log") != inter.channel_id:
            await send_log(inter.client, "mod_log", e)
