import time
import discord
from discord.ext import commands
from discord import app_commands
from config import PERMS_CURATOR
from storage import get_user, set_user, get_shop, save_shop, get_settings
from helpers import is_curator, is_staff, send_log

RED = 0x9B111E

SHOPS = {
    "players": {"emoji": "👥", "name": "Магазин игроков"},
    "staff":   {"emoji": "🛠️", "name": "Магазин персонала"},
}
CURRENCY = {
    "points": {"emoji": "🪙", "name": "баллов"},
    "salary": {"emoji": "💵", "name": "ЗП"},
}
DEFAULT_CURRENCY = {"players": "points", "staff": "salary"}
STAFF_CAN_USE_PLAYER_SHOP = True
MAX_ITEMS_PER_SHOP = 25 

CUR_ALIASES = {
    "баллы": "points", "балл": "points", "баллов": "points", "points": "points",
    "зп": "salary", "salary": "salary", "зарплата": "salary",
}


def num(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def item_shop(item): return item.get("shop", "players")
def item_currency(item): return item.get("currency", "points")  # старые товары были за баллы


def price_text(item) -> str:
    return f"{CURRENCY[item_currency(item)]['emoji']} {num(item['price'])}"


def items_of(shop_key):
    return {iid: it for iid, it in get_shop().items() if item_shop(it) == shop_key}


def allowed_shops(member):
    if is_staff(member):
        return ["staff", "players"] if STAFF_CAN_USE_PLAYER_SHOP else ["staff"]
    return ["players"]


def build_shop_embed(member, shop_key) -> discord.Embed:
    meta = SHOPS[shop_key]
    items = items_of(shop_key)
    e = discord.Embed(title=f"{meta['emoji']} {meta['name']}", color=RED, timestamp=discord.utils.utcnow())
    e.set_author(name="Ashra Team • Discord Server")

    head = "Выберите товар в меню ниже.\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
    if items:
        lines = [f"**{n}.** {it['name']} — **{price_text(it)}**" + (" 📝" if it.get("prompt") else "")
                 for n, it in enumerate(items.values(), start=1)]
        e.description = head + "\n".join(lines) + "\n\n📝 — при покупке нужно уточнить детали"
    else:
        e.description = head + "Здесь пока пусто."

    u = get_user(member.id)
    e.set_footer(text=f"Ваш баланс: 🪙 {num(u['points'])} · 💵 {num(u['salary'])}")
    return e


class ShopTabButton(discord.ui.Button):
    def __init__(self, key, current):
        meta = SHOPS[key]
        super().__init__(label=meta["name"], emoji=meta["emoji"], row=0,
                         style=discord.ButtonStyle.primary if key == current else discord.ButtonStyle.secondary,
                         disabled=(key == current))
        self.key = key

    async def callback(self, i: discord.Interaction):
        v: ShopView = self.view
        v.shop_key = self.key
        v.rebuild()
        await i.response.edit_message(embed=build_shop_embed(i.user, self.key), view=v)


class ShopSelect(discord.ui.Select):
    def __init__(self, shop_key, items):
        opts = [discord.SelectOption(label=it["name"][:80], value=iid, description=f"Цена: {num(it['price'])} {CURRENCY[item_currency(it)]['name']}",
                                     emoji=CURRENCY[item_currency(it)]["emoji"])
                for iid, it in list(items.items())[:MAX_ITEMS_PER_SHOP]]
        super().__init__(placeholder="Выберите товар для покупки…", options=opts, row=1)

    async def callback(self, i: discord.Interaction):
        item = get_shop().get(self.values[0])
        if not item:
            return await i.response.send_message("❌ Этот товар больше недоступен.", ephemeral=True)
        if item_shop(item) not in allowed_shops(i.user):
            return await i.response.send_message("❌ Этот магазин вам недоступен.", ephemeral=True)
        cur = item_currency(item)
        have = get_user(i.user.id)[cur]
        if have < item["price"]:
            return await i.response.send_message(
                f"❌ Недостаточно {CURRENCY[cur]['name']}: у вас {num(have)}, нужно {num(item['price'])}.", ephemeral=True)
        if item.get("prompt"):
            await i.response.send_modal(PurchaseModal(item, item["prompt"], self.view))
        else:
            await process_purchase(i, item, "Не требуется", self.view)


class ShopView(discord.ui.View):
    def __init__(self, inter: discord.Interaction, shop_key: str):
        super().__init__(timeout=180)
        self.inter, self.shop_key = inter, shop_key
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        allowed = allowed_shops(self.inter.user)
        if len(allowed) > 1:
            for key in allowed:
                self.add_item(ShopTabButton(key, self.shop_key))
        items = items_of(self.shop_key)
        if items:
            self.add_item(ShopSelect(self.shop_key, items))

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.inter.user.id:
            await i.response.send_message("❌ Это меню открыл другой участник. Откройте своё через `/shop`.", ephemeral=True)
            return False
        return True

    async def refresh(self):
        self.rebuild()
        try:
            await self.inter.edit_original_response(embed=build_shop_embed(self.inter.user, self.shop_key), view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass


async def _reply(inter: discord.Interaction, msg: str):
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=True)
    else:
        await inter.response.send_message(msg, ephemeral=True)


async def process_purchase(inter: discord.Interaction, item, details, panel=None):
    price, cur = item["price"], item_currency(item)
    if price <= 0:
        return await _reply(inter, "❌ У товара некорректная цена.")
    if item_shop(item) not in allowed_shops(inter.user):
        return await _reply(inter, "❌ Этот магазин вам недоступен.")
    u = get_user(inter.user.id)
    if u[cur] < price:
        return await _reply(inter, f"❌ Недостаточно {CURRENCY[cur]['name']}: у вас {num(u[cur])}.")
    set_user(inter.user.id, **{cur: u[cur] - price})

    e = discord.Embed(title="🛒 Покупка", color=RED, timestamp=discord.utils.utcnow())
    e.add_field(name="Кто", value=inter.user.mention, inline=True)
    e.add_field(name="Что", value=item["name"], inline=True)
    e.add_field(name="Цена", value=price_text(item), inline=True)
    e.add_field(name="Магазин", value=SHOPS[item_shop(item)]["name"], inline=True)
    e.add_field(name="Детали", value=details, inline=False)
    await send_log(inter.client, "purchase_log", e)

    await _reply(inter, f"✅ Куплено: **{item['name']}**\n📝 Заказ: {details}")
    if panel:
        await panel.refresh()


class PurchaseModal(discord.ui.Modal):
    def __init__(self, item, prompt, panel):
        super().__init__(title="Оформление заказа")
        self.item, self.panel = item, panel
        self.det = discord.ui.TextInput(label=prompt[:45], style=discord.TextStyle.paragraph,
                                        required=True, max_length=300)
        self.add_item(self.det)

    async def on_submit(self, inter: discord.Interaction):
        await process_purchase(inter, self.item, self.det.value, self.panel)


def build_admin_embed() -> discord.Embed:
    e = discord.Embed(
        title="🛠️ Управление магазинами",
        description="Добавляйте и удаляйте товары.\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        color=RED, timestamp=discord.utils.utcnow())
    for key, meta in SHOPS.items():
        items = items_of(key)
        text = "\n".join(f"• {it['name']} — {price_text(it)}" for it in items.values()) or "`пусто`"
        e.add_field(name=f"{meta['emoji']} {meta['name']} ({len(items)}/{MAX_ITEMS_PER_SHOP})",
                    value=text[:1024], inline=True)
    return e


class CuratorView(discord.ui.View):
    async def interaction_check(self, i: discord.Interaction) -> bool:
        if not is_curator(i.user):
            await i.response.send_message("❌ Нет прав.", ephemeral=True)
            return False
        return True


class ShopAddModal(discord.ui.Modal):
    def __init__(self, shop_key, panel):
        super().__init__(title=f"Добавить: {SHOPS[shop_key]['name']}"[:45])
        self.shop_key, self.panel = shop_key, panel
        self.s_name = discord.ui.TextInput(label="Название", max_length=100)
        self.s_price = discord.ui.TextInput(label="Цена", max_length=10)
        default = "баллы" if DEFAULT_CURRENCY[shop_key] == "points" else "зп"
        self.s_cur = discord.ui.TextInput(label="Валюта: баллы или зп", required=False, max_length=10,
                                          placeholder=f"Пусто = {default}")
        self.s_prompt = discord.ui.TextInput(label="Вопрос при покупке (необязательно)", required=False,
                                             max_length=100, placeholder="Например: Укажите ник и количество")
        for f in (self.s_name, self.s_price, self.s_cur, self.s_prompt):
            self.add_item(f)

    async def on_submit(self, inter: discord.Interaction):
        if not is_curator(inter.user):
            return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        try:
            price = int(self.s_price.value.replace(" ", ""))
        except ValueError:
            return await inter.response.send_message("❌ Цена — число.", ephemeral=True)
        if price <= 0:
            return await inter.response.send_message("❌ Цена должна быть больше 0.", ephemeral=True)

        raw = self.s_cur.value.strip().lower()
        if raw and raw not in CUR_ALIASES:
            return await inter.response.send_message("❌ Валюта: «баллы» или «зп».", ephemeral=True)
        currency = CUR_ALIASES[raw] if raw else DEFAULT_CURRENCY[self.shop_key]

        if len(items_of(self.shop_key)) >= MAX_ITEMS_PER_SHOP:
            return await inter.response.send_message(
                f"❌ В магазине уже {MAX_ITEMS_PER_SHOP} товаров, это максимум.", ephemeral=True)

        shop = get_shop()
        shop[str(time.time_ns())] = {
            "name": self.s_name.value, "price": price, "prompt": self.s_prompt.value or None,
            "shop": self.shop_key, "currency": currency,
        }
        save_shop(shop)
        await inter.response.send_message(
            f"✅ **{self.s_name.value}** добавлен в «{SHOPS[self.shop_key]['name']}» — "
            f"{CURRENCY[currency]['emoji']} {num(price)}.", ephemeral=True)
        await self.panel.refresh()


class ShopRemoveSelect(discord.ui.Select):
    def __init__(self, shop_key, panel):
        self.panel = panel
        opts = [discord.SelectOption(label=it["name"][:80], value=iid, description=price_text(it)[:100])
                for iid, it in list(items_of(shop_key).items())[:MAX_ITEMS_PER_SHOP]]
        super().__init__(placeholder="Что удалить?", options=opts)

    async def callback(self, i: discord.Interaction):
        shop = get_shop()
        item = shop.pop(self.values[0], None)
        save_shop(shop)
        name = item["name"] if item else "Товар"
        await i.response.edit_message(content=f"✅ **{name}** удалён.", view=None)
        await self.panel.refresh()


class ShopRemoveView(CuratorView):
    def __init__(self, shop_key, panel):
        super().__init__(timeout=60)
        self.add_item(ShopRemoveSelect(shop_key, panel))


class ShopAdminView(CuratorView):
    def __init__(self, inter: discord.Interaction):
        super().__init__(timeout=300)
        self.inter = inter

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.inter.user.id:
            await i.response.send_message("❌ Это не ваша панель.", ephemeral=True)
            return False
        return await super().interaction_check(i)

    async def refresh(self):
        try:
            await self.inter.edit_original_response(embed=build_admin_embed(), view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    async def _remove(self, i: discord.Interaction, key):
        if not items_of(key):
            return await i.response.send_message("В этом магазине пусто.", ephemeral=True)
        await i.response.send_message("Выберите товар:", view=ShopRemoveView(key, self), ephemeral=True)

    @discord.ui.button(label="Добавить: игроки", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_players(self, i, b): await i.response.send_modal(ShopAddModal("players", self))

    @discord.ui.button(label="Добавить: персонал", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_staff(self, i, b): await i.response.send_modal(ShopAddModal("staff", self))

    @discord.ui.button(label="Удалить: игроки", emoji="🗑", style=discord.ButtonStyle.danger, row=1)
    async def rem_players(self, i, b): await self._remove(i, "players")

    @discord.ui.button(label="Удалить: персонал", emoji="🗑", style=discord.ButtonStyle.danger, row=1)
    async def rem_staff(self, i, b): await self._remove(i, "staff")

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def reload(self, i: discord.Interaction, b):
        await i.response.edit_message(embed=build_admin_embed(), view=self)


# ═══════════════ КОМАНДЫ ═══════════════
class ShopCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="shop", description="Магазин")
    @app_commands.guild_only()
    async def shop_cmd(self, inter: discord.Interaction):
        start = allowed_shops(inter.user)[0]
        view = ShopView(inter, start)
        await inter.response.send_message(embed=build_shop_embed(inter.user, start), view=view, ephemeral=True)

    @app_commands.command(name="adm_shop", description="Управление магазинами (куратор)")
    @app_commands.guild_only()
    @app_commands.default_permissions(**PERMS_CURATOR)
    async def adm_shop_cmd(self, inter: discord.Interaction):
        if not is_curator(inter.user):
            return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        view = ShopAdminView(inter)
        await inter.response.send_message(embed=build_admin_embed(), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(ShopCog(bot))
