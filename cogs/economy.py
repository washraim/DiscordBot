import asyncio
import time
import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import BG_PATH
from storage import (
    get_user, set_user, add_stats, get_shop, save_shop, get_settings, all_users
)
from helpers import is_curator, is_responsible
from image_gen import generate_profile_card, warm_up, OUT_EXT

XP_COOLDOWN = 30
XP_PER_MESSAGE = 15
VOICE_TICK = 300


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._msg_count = {}
        self._last_xp = {}
        self.voice_loop.start()

    async def cog_load(self):
        await asyncio.to_thread(warm_up, BG_PATH)

    def cog_unload(self):
        self.voice_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        uid = message.author.id
        now = time.monotonic()

        staff = is_responsible(message.author)

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
                    continue
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

    @app_commands.command(name="shop", description="Магазин")
    async def shop_cmd(self, inter: discord.Interaction):
        shop = get_shop()
        if not shop: return await inter.response.send_message("Магазин пуст.", ephemeral=True)
        v = discord.ui.View(timeout=120)
        v.add_item(ShopSelect(shop))
        await inter.response.send_message("🛒 **Магазин**", view=v, ephemeral=True)

    @app_commands.command(name="adm_shop", description="Управление магазином")
    async def adm_shop_cmd(self, inter: discord.Interaction):
        if not is_curator(inter.user): return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        e = discord.Embed(title="🛒 Панель магазина", description="Настройка товаров", color=0x9B111E)
        await inter.response.send_message(embed=e, view=ShopAdminView(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))


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


async def _reply(inter: discord.Interaction, msg: str):
    """Ответить правильно и до, и после первого ответа на interaction."""
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=True)
    else:
        await inter.response.send_message(msg, ephemeral=True)


class ShopSelect(discord.ui.Select):
    def __init__(self, shop):
        opts = [discord.SelectOption(label=i["name"][:80], value=iid, description=f"Цена: {i['price']}")
                for iid, i in shop.items()]
        super().__init__(placeholder="Выберите предмет...", options=opts[:25])

    async def callback(self, inter: discord.Interaction):
        item = get_shop().get(self.values[0])
        if not item:
            return await inter.response.send_message("❌ Этот товар больше недоступен.", ephemeral=True)
        u = get_user(inter.user.id)
        if u["points"] < item["price"]:
            return await inter.response.send_message(f"❌ Мало баллов (у вас {u['points']}).", ephemeral=True)
        if item.get("prompt"):
            await inter.response.send_modal(PurchaseModal(item, item["prompt"]))
        else:
            await process_purchase(inter, item, "Не требуется")


async def process_purchase(inter: discord.Interaction, item, details):
    price = item["price"]
    if price <= 0:
        return await _reply(inter, "❌ У товара некорректная цена.")
    u = get_user(inter.user.id)
    if u["points"] < price:
        return await _reply(inter, f"❌ Мало баллов (у вас {u['points']}).")
    set_user(inter.user.id, points=u["points"] - price)

    ch_id = get_settings().get("purchase_log")
    if ch_id and (ch := inter.client.get_channel(ch_id)):
        e = discord.Embed(title="🛒 Покупка", color=0x9B111E)
        e.add_field(name="Кто", value=inter.user.mention, inline=True)
        e.add_field(name="Что", value=item["name"], inline=True)
        e.add_field(name="Цена", value=f"{price}", inline=True)
        e.add_field(name="Детали", value=details, inline=False)
        try:
            await ch.send(embed=e)
        except discord.HTTPException:
            pass

    await _reply(inter, f"✅ Куплено: **{item['name']}**\n📝 Заказ: {details}")


class PurchaseModal(discord.ui.Modal):
    def __init__(self, item, prompt):
        super().__init__(title="Оформление заказа")
        self.item = item
        self.det = discord.ui.TextInput(label=prompt[:45], style=discord.TextStyle.paragraph,
                                        required=True, max_length=300)
        self.add_item(self.det)

    async def on_submit(self, inter: discord.Interaction):
        await process_purchase(inter, self.item, self.det.value)


class ShopAddModal(discord.ui.Modal, title="Добавить товар"):
    s_name = discord.ui.TextInput(label="Название", max_length=100)
    s_price = discord.ui.TextInput(label="Цена", max_length=10)
    s_prompt = discord.ui.TextInput(label="Вопрос при покупке (опционально)", required=False, max_length=100,
                                    placeholder="Например: Укажите покемона, скин и кол-во")

    async def on_submit(self, inter: discord.Interaction):
        try:
            price = int(self.s_price.value)
        except ValueError:
            return await inter.response.send_message("❌ Цена — число.", ephemeral=True)
        if price <= 0:
            return await inter.response.send_message("❌ Цена должна быть больше 0.", ephemeral=True)
        shop = get_shop()
        shop[str(time.time_ns())] = {"name": self.s_name.value, "price": price,
                                     "prompt": self.s_prompt.value or None}
        save_shop(shop)
        await inter.response.send_message(f"✅ **{self.s_name.value}** добавлен.", ephemeral=True)


class ShopAdminView(discord.ui.View):
    def __init__(self): super().__init__(timeout=180)

    @discord.ui.button(label="➕ Добавить", style=discord.ButtonStyle.success)
    async def add_btn(self, i, b): await i.response.send_modal(ShopAddModal())

    @discord.ui.button(label="🗑 Удалить", style=discord.ButtonStyle.danger)
    async def rem_btn(self, i, b):
        shop = get_shop()
        if not shop: return await i.response.send_message("Пусто.", ephemeral=True)
        opts = [discord.SelectOption(label=item["name"][:80], value=iid) for iid, item in shop.items()]
        await i.response.send_message("Выберите:", view=ShopRemoveView(opts), ephemeral=True)


class ShopRemoveView(discord.ui.View):
    def __init__(self, opts):
        super().__init__(timeout=60)
        self.add_item(ShopRemoveSelect(opts[:25]))


class ShopRemoveSelect(discord.ui.Select):
    def __init__(self, opts):
        super().__init__(placeholder="Что удалить?", options=opts)

    async def callback(self, i: discord.Interaction):
        shop = get_shop()
        shop.pop(self.values[0], None)
        save_shop(shop)
        await i.response.edit_message(content="✅ Удалено.", view=None)
