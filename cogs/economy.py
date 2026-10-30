import asyncio
import time
import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import BG_PATH
from storage import get_user, add_stats, all_users
from helpers import is_responsible
from image_gen import generate_profile_card, warm_up, OUT_EXT

XP_COOLDOWN = 30        # сек: не чаще, чем раз в 30 сек даём XP и счётчик на баллы
XP_PER_MESSAGE = 15
VOICE_TICK = 300        # сек


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._msg_count = {}
        self._last_xp = {}
        self.voice_loop.start()

    async def cog_load(self):
        # Прогреваем фон и шрифты при старте, чтобы первый /profile не тормозил
        await asyncio.to_thread(warm_up, BG_PATH)

    def cog_unload(self):
        self.voice_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        uid = message.author.id
        now = time.monotonic()

        # Персонал (владелец, кураторы, ответственные) получает статистику и XP,
        # но не получает баллы: им платят ЗП
        staff = is_responsible(message.author)

        # Сообщение считаем всегда, а XP и баллы только с кулдауном (защита от спама)
        if now - self._last_xp.get(uid, 0) < XP_COOLDOWN:
            add_stats(uid, messages=1)
            return
        self._last_xp[uid] = now

        pts = 0
        if not staff:
            self._msg_count[uid] = self._msg_count.get(uid, 0) + 1
            if self._msg_count[uid] >= 5:
                self._msg_count[uid] = 0
                pts = 1
        add_stats(uid, messages=1, xp=XP_PER_MESSAGE, points=pts)

    @tasks.loop(minutes=5)
    async def voice_loop(self):
        for guild in self.bot.guilds:
            afk = guild.afk_channel
            for vc in guild.voice_channels:
                if afk and vc.id == afk.id:
                    continue  # AFK-канал не считаем
                for m in vc.members:
                    v = m.voice
                    if m.bot or not v or v.self_deaf or v.deaf:
                        continue
                    add_stats(m.id, voice_time=VOICE_TICK, xp=30, points=2)

    @voice_loop.before_loop
    async def _bv(self): await self.bot.wait_until_ready()

    @app_commands.command(name="profile", description="Ваш профиль")
    async def profile_cmd(self, inter: discord.Interaction, member: discord.Member = None):
        await inter.response.defer()
        m = member or inter.user
        u = get_user(m.id)
        # Полный рейтинг по времени в войсе (без обрезки), чтобы место считалось для всех
        top_users = sorted(all_users().items(), key=lambda x: x[1].get("voice_time", 0), reverse=True)

        try:
            image_file = await generate_profile_card(BG_PATH, m, u, top_users, inter.guild)
            if image_file:
                file = discord.File(fp=image_file, filename=f"profile.{OUT_EXT}")
                embed = discord.Embed(color=0x9B111E)
                embed.description = "\u200b"
                embed.set_image(url=f"attachment://profile.{OUT_EXT}")
                await inter.followup.send(embed=embed, file=file)
            else:
                await inter.followup.send("❌ Ошибка: файл `bg.png` не найден.", ephemeral=True)
        except Exception as e:
            await inter.followup.send(f"❌ Ошибка: `{e}`", ephemeral=True)

    @app_commands.command(name="balance", description="Показать ваш баланс")
    async def balance_cmd(self, inter: discord.Interaction, member: discord.Member = None):
        m = member or inter.user
        u = get_user(m.id)
        view = BalanceView(m, u["points"], u["salary"])
        await inter.response.send_message(view=view)


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))


# ═══════════════ КАРТОЧКА БАЛАНСА ═══════════════
class BalanceView(discord.ui.LayoutView):
    def __init__(self, member: discord.Member, points: int, salary: int):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=discord.Color.from_rgb(35, 39, 42))

        header = discord.ui.Section(
            discord.ui.TextDisplay(f"## Текущий баланс —\n{member.display_name}"),
            accessory=discord.ui.Thumbnail(member.display_avatar.url)
        )
        container.add_item(header)
        container.add_item(discord.ui.Separator())

        container.add_item(discord.ui.TextDisplay("### > **Баллы:**"))
        container.add_item(discord.ui.TextDisplay(f"### >>> {points}"))

        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        container.add_item(discord.ui.TextDisplay("### > **ЗП:**"))
        container.add_item(discord.ui.TextDisplay(f"### >>> {salary}"))

        self.add_item(container)
