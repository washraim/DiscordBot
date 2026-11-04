import time
import discord
from discord.ext import commands
from discord import app_commands
from config import PERMS_CURATOR
from storage import (
    get_user, set_user, get_shop, save_shop, get_settings,
    create_order, get_order, update_order, delete_order, orders_of,
)
from helpers import is_curator, is_staff

RED = 0x9B111E
GREEN = 0x2ECC71
ORANGE = 0xE67E22
BLUE = 0x3498DB

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

STATUS = {
    "new":      ("🆕 Новый", BLUE),
    "accepted": ("🟡 Принят", ORANGE),
    "done":     ("✅ Выполнен", GREEN),
    "rejected": ("❌ Отклонён", RED),
}


def num(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def ts(t, style="R") -> str:
    return f"<t:{int(t)}:{style}>"


def item_shop(item): return item.get("shop", "players")
def item_currency(item): return item.get("currency", "points")


def price_text(item) -> str:
    return f"{CURRENCY[item_currency(item)]['emoji']} {num(item['price'])}"


def limits_text(item) -> str:
    parts = []
    stock = item.get("stock")
    if stock is not None:
        parts.append("⛔ нет в наличии" if stock <= 0 else f"осталось {stock} шт.")
    if item.get("cooldown_days"):
        parts.append(f"раз в {item['cooldown_days']} дн.")
    return " · ".join(parts)


def items_of(shop_key):
    return {iid: it for iid, it in get_shop().items() if item_shop(it) == shop_key}


def allowed_shops(member):
    if is_staff(member):
        return ["staff", "players"] if STAFF_CAN_USE_PLAYER_SHOP else ["staff"]
    return ["players"]


def cooldown_until(user_id, item_id, item) -> float:
    days = item.get("cooldown_days")
    if not days:
        return 0
    last = max((o["created"] for o in orders_of(user_id)
                if o.get("item_id") == item_id and o["status"] != "rejected"), default=None)
    if last is None:
        return 0
    until = last + days * 86400
    return until if until > time.time() else 0


def validate_purchase(member, item_id, item):
    if item["price"] <= 0:
        return "❌ У товара некорректная цена."
    if item_shop(item) not in allowed_shops(member):
        return "❌ Этот магазин вам недоступен."
    stock = item.get("stock")
    if stock is not None and stock <= 0:
        return "❌ Товар закончился."
    until = cooldown_until(member.id, item_id, item)
    if until:
        return f"❌ Этот товар можно покупать раз в {item['cooldown_days']} дн. Снова будет доступен {ts(until)}."
    cur = item_currency(item)
    have = get_user(member.id)[cur]
    if have < item["price"]:
        return f"❌ Недостаточно {CURRENCY[cur]['name']}: у вас {num(have)}, нужно {num(item['price'])}."
    return None


def _change_stock(item_id, delta):
    shop = get_shop()
    it = shop.get(item_id)
    if it is not None and it.get("stock") is not None:
        it["stock"] = max(0, it["stock"] + delta)
        save_shop(shop)


def build_shop_embed(member, shop_key) -> discord.Embed:
    meta = SHOPS[shop_key]
    items = items_of(shop_key)
    e = discord.Embed(title=f"{meta['emoji']} {meta['name']}", color=RED, timestamp=discord.utils.utcnow())
    e.set_author(name="Ashra Team • Discord Server")

    head = "Выберите товар в меню ниже.\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
    if items:
        lines = []
        for n, it in enumerate(items.values(), start=1):
            line = f"**{n}.** {it['name']} — **{price_text(it)}**"
            if limits_text(it):
                line += f" · {limits_text(it)}"
            if it.get("prompt"):
                line += " 📝"
            lines.append(line)
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
    def __init__(self, items):
        opts = []
        for iid, it in items.items():
            if it.get("stock") is not None and it["stock"] <= 0:
                continue
            cur = CURRENCY[item_currency(it)]
            desc = f"Цена: {num(it['price'])} {cur['name']}"
            if limits_text(it):
                desc += f" · {limits_text(it)}"
            opts.append(discord.SelectOption(label=it["name"][:80], value=iid, description=desc[:100],
                                             emoji=cur["emoji"]))
        super().__init__(placeholder="Выберите товар для покупки…", options=opts[:MAX_ITEMS_PER_SHOP], row=1)

    async def callback(self, i: discord.Interaction):
        item_id = self.values[0]
        item = get_shop().get(item_id)
        if not item:
            return await i.response.send_message("❌ Этот товар больше недоступен.", ephemeral=True)
        err = validate_purchase(i.user, item_id, item)
        if err:
            return await i.response.send_message(err, ephemeral=True)
        if item.get("prompt"):
            await i.response.send_modal(PurchaseModal(item_id, item["prompt"], self.view))
        else:
            await process_purchase(i, item_id, "Не требуется", self.view)


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
        sel = ShopSelect(items_of(self.shop_key))
        if sel.options:
            self.add_item(sel)

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


def build_order_embed(o) -> discord.Embed:
    label, color = STATUS[o["status"]]
    cur = CURRENCY[o["currency"]]
    e = discord.Embed(title=f"🛒 Заказ #{o['id']}", color=color, timestamp=discord.utils.utcnow())
    e.add_field(name="Кто", value=f"<@{o['user_id']}>\n`{o['user_id']}`", inline=True)
    e.add_field(name="Что", value=o["name"], inline=True)
    e.add_field(name="Цена", value=f"{cur['emoji']} {num(o['price'])}", inline=True)
    e.add_field(name="Магазин", value=SHOPS[o["shop"]]["name"], inline=True)
    e.add_field(name="Статус", value=label, inline=True)
    if o.get("handled_by"):
        e.add_field(name="Обработал", value=f"<@{o['handled_by']}> · {ts(o['handled_at'])}", inline=True)
    e.add_field(name="Детали", value=(o.get("details") or "—")[:1000], inline=False)
    if o.get("reason"):
        e.add_field(name="Причина отказа", value=o["reason"][:500], inline=False)
    if o["status"] == "rejected":
        e.add_field(name="Возврат", value=f"{cur['emoji']} {num(o['price'])} возвращено покупателю", inline=False)
    e.set_footer(text=f"Заказ создан {time.strftime('%d.%m.%Y %H:%M', time.localtime(o['created']))}")
    return e


BTN = {
    "accept": ("Принять",    "🟡", discord.ButtonStyle.primary),
    "done":   ("Выполнено",  "✅", discord.ButtonStyle.success),
    "reject": ("Отклонить",  "❌", discord.ButtonStyle.danger),
}


class OrderButton(discord.ui.DynamicItem[discord.ui.Button],
                  template=r"order:(?P<id>\d+):(?P<act>accept|done|reject)"):

    def __init__(self, oid: int, act: str):
        label, emoji, style = BTN[act]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"order:{oid}:{act}"))
        self.oid, self.act = oid, act

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["id"]), match["act"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_curator(interaction.user):
            await interaction.response.send_message("❌ Заказы обрабатывают только кураторы.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction):
        o = get_order(self.oid)
        if o is None:
            return await interaction.response.send_message("❌ Заказ не найден.", ephemeral=True)
        if o["status"] in ("done", "rejected"):
            return await interaction.response.send_message(
                f"ℹ️ Заказ уже обработан: {STATUS[o['status']][0]}.", ephemeral=True)
        if self.act == "reject":
            return await interaction.response.send_modal(RejectModal(self.oid))
        await apply_status(interaction, self.oid, self.act)


def order_view(oid, status):
    if status in ("done", "rejected"):
        return None
    v = discord.ui.View(timeout=None)
    if status == "new":
        v.add_item(OrderButton(oid, "accept"))
    v.add_item(OrderButton(oid, "done"))
    v.add_item(OrderButton(oid, "reject"))
    return v


class RejectModal(discord.ui.Modal, title="Отклонить заказ"):
    reason = discord.ui.TextInput(label="Причина (необязательно)", style=discord.TextStyle.paragraph,
                                  required=False, max_length=300)

    def __init__(self, oid):
        super().__init__()
        self.oid = oid

    async def on_submit(self, i: discord.Interaction):
        await apply_status(i, self.oid, "reject", self.reason.value.strip() or None)


async def apply_status(inter: discord.Interaction, oid: int, act: str, reason=None):
    if not is_curator(inter.user):
        return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
    o = get_order(oid)
    if o is None:
        return await inter.response.send_message("❌ Заказ не найден.", ephemeral=True)
    if o["status"] in ("done", "rejected"):
        return await inter.response.send_message(f"ℹ️ Заказ уже обработан: {STATUS[o['status']][0]}.", ephemeral=True)
    if act == "accept" and o["status"] != "new":
        return await inter.response.send_message("ℹ️ Заказ уже принят.", ephemeral=True)

    new_status = {"accept": "accepted", "done": "done", "reject": "rejected"}[act]
    if act == "reject":
        u = get_user(o["user_id"])
        set_user(o["user_id"], **{o["currency"]: u[o["currency"]] + o["price"]})
        _change_stock(o["item_id"], +1)

    o = update_order(oid, status=new_status, handled_by=inter.user.id, handled_at=time.time(), reason=reason)

    msg = inter.message
    if msg is None and o.get("channel_id") and o.get("message_id"):
        ch = inter.client.get_channel(o["channel_id"])
        try:
            msg = await ch.fetch_message(o["message_id"]) if ch else None
        except discord.HTTPException:
            msg = None
    if msg is not None:
        try:
            await msg.edit(embed=build_order_embed(o), view=order_view(oid, new_status))
        except discord.HTTPException:
            pass

    note = " Средства возвращены покупателю." if act == "reject" else ""
    await inter.response.send_message(f"✅ Заказ #{oid}: {STATUS[new_status][0]}.{note}", ephemeral=True)
    await notify_buyer(inter.client, o)


async def notify_buyer(client, o):
    titles = {
        "accepted": ("🟡 Заказ принят в работу", ORANGE),
        "done": ("✅ Заказ выполнен", GREEN),
        "rejected": ("❌ Заказ отклонён", RED),
    }
    if o["status"] not in titles:
        return
    title, color = titles[o["status"]]
    cur = CURRENCY[o["currency"]]
    e = discord.Embed(title=f"{title} (#{o['id']})", color=color, timestamp=discord.utils.utcnow())
    e.add_field(name="Товар", value=o["name"], inline=True)
    if o["status"] == "rejected":
        e.add_field(name="Возврат", value=f"{cur['emoji']} {num(o['price'])} {cur['name']}", inline=True)
        if o.get("reason"):
            e.add_field(name="Причина", value=o["reason"][:500], inline=False)
    try:
        user = client.get_user(o["user_id"]) or await client.fetch_user(o["user_id"])
        await user.send(embed=e)
    except discord.HTTPException:
        pass


async def _reply(inter: discord.Interaction, msg: str):
    if inter.response.is_done():
        await inter.followup.send(msg, ephemeral=True)
    else:
        await inter.response.send_message(msg, ephemeral=True)


async def process_purchase(inter: discord.Interaction, item_id: str, details: str, panel=None):
    item = get_shop().get(item_id)
    if not item:
        return await _reply(inter, "❌ Этот товар больше недоступен.")
    err = validate_purchase(inter.user, item_id, item)
    if err:
        return await _reply(inter, err)

    ch_id = get_settings().get("purchase_log")
    ch = inter.client.get_channel(ch_id) if ch_id else None
    if ch is None:
        return await _reply(inter, "❌ Магазин временно недоступен: не настроен канал покупок. Сообщите куратору.")

    cur, price = item_currency(item), item["price"]
    u = get_user(inter.user.id)
    set_user(inter.user.id, **{cur: u[cur] - price})
    _change_stock(item_id, -1)
    oid = create_order(user_id=inter.user.id, item_id=item_id, name=item["name"], price=price,
                       currency=cur, shop=item_shop(item), details=details)
    try:
        msg = await ch.send(embed=build_order_embed(get_order(oid)), view=order_view(oid, "new"))
    except discord.HTTPException:
        u = get_user(inter.user.id)
        set_user(inter.user.id, **{cur: u[cur] + price})
        _change_stock(item_id, +1)
        delete_order(oid)
        return await _reply(inter, "❌ Не удалось оформить заказ (бот не может писать в канал покупок). Средства не списаны.")
    update_order(oid, channel_id=ch.id, message_id=msg.id)

    await _reply(inter, f"✅ Куплено: **{item['name']}**\n🧾 Заказ **#{oid}**\n📝 {details}\n"
                        "О статусе заказа придёт уведомление в личные сообщения, список заказов: `/orders`.")
    if panel:
        await panel.refresh()


class PurchaseModal(discord.ui.Modal):
    def __init__(self, item_id, prompt, panel):
        super().__init__(title="Оформление заказа")
        self.item_id, self.panel = item_id, panel
        self.det = discord.ui.TextInput(label=prompt[:45], style=discord.TextStyle.paragraph,
                                        required=True, max_length=300)
        self.add_item(self.det)

    async def on_submit(self, inter: discord.Interaction):
        await process_purchase(inter, self.item_id, self.det.value, self.panel)


def build_admin_embed() -> discord.Embed:
    e = discord.Embed(
        title="🛠️ Управление магазинами",
        description="Добавляйте, удаляйте товары и настраивайте лимиты.\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        color=RED, timestamp=discord.utils.utcnow())
    for key, meta in SHOPS.items():
        items = items_of(key)
        lines = []
        for it in items.values():
            line = f"• {it['name']} — {price_text(it)}"
            if limits_text(it):
                line += f" ({limits_text(it)})"
            lines.append(line)
        e.add_field(name=f"{meta['emoji']} {meta['name']} ({len(items)}/{MAX_ITEMS_PER_SHOP})",
                    value=("\n".join(lines) or "`пусто`")[:1024], inline=True)
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
            "shop": self.shop_key, "currency": currency, "stock": None, "cooldown_days": None,
        }
        save_shop(shop)
        await inter.response.send_message(
            f"✅ **{self.s_name.value}** добавлен в «{SHOPS[self.shop_key]['name']}» — "
            f"{CURRENCY[currency]['emoji']} {num(price)}. Лимиты задаются кнопкой «Лимиты».", ephemeral=True)
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


class LimitsModal(discord.ui.Modal):
    def __init__(self, item_id, item, panel):
        super().__init__(title=f"Лимиты: {item['name']}"[:45])
        self.item_id, self.panel = item_id, panel
        stock, cd = item.get("stock"), item.get("cooldown_days")
        self.stock = discord.ui.TextInput(label="Осталось штук (пусто = без лимита)", required=False, max_length=6,
                                          default=str(stock) if stock is not None else None)
        self.cd = discord.ui.TextInput(label="Раз в N дней на человека (пусто = нет)", required=False, max_length=3,
                                       default=str(cd) if cd else None)
        self.add_item(self.stock)
        self.add_item(self.cd)

    async def on_submit(self, inter: discord.Interaction):
        if not is_curator(inter.user):
            return await inter.response.send_message("❌ Нет прав.", ephemeral=True)

        def parse(raw):
            raw = raw.strip()
            if not raw:
                return None
            return int(raw)

        try:
            stock, cd = parse(self.stock.value), parse(self.cd.value)
        except ValueError:
            return await inter.response.send_message("❌ Нужны целые числа (или пустые поля).", ephemeral=True)
        if (stock is not None and stock < 0) or (cd is not None and cd < 0):
            return await inter.response.send_message("❌ Числа не могут быть отрицательными.", ephemeral=True)

        shop = get_shop()
        it = shop.get(self.item_id)
        if it is None:
            return await inter.response.send_message("❌ Товар уже удалён.", ephemeral=True)
        it["stock"] = stock
        it["cooldown_days"] = cd or None
        save_shop(shop)
        txt = limits_text(it) or "без ограничений"
        await inter.response.send_message(f"✅ **{it['name']}**: {txt}.", ephemeral=True)
        await self.panel.refresh()


class LimitsSelect(discord.ui.Select):
    def __init__(self, shop_key, panel):
        self.panel = panel
        opts = [discord.SelectOption(label=it["name"][:80], value=iid,
                                     description=(limits_text(it) or "без ограничений")[:100])
                for iid, it in list(items_of(shop_key).items())[:MAX_ITEMS_PER_SHOP]]
        super().__init__(placeholder="Для какого товара настроить лимиты?", options=opts)

    async def callback(self, i: discord.Interaction):
        item = get_shop().get(self.values[0])
        if not item:
            return await i.response.edit_message(content="❌ Товар уже удалён.", view=None)
        await i.response.send_modal(LimitsModal(self.values[0], item, self.panel))


class LimitsView(CuratorView):
    def __init__(self, shop_key, panel):
        super().__init__(timeout=60)
        self.add_item(LimitsSelect(shop_key, panel))


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

    async def _limits(self, i: discord.Interaction, key):
        if not items_of(key):
            return await i.response.send_message("В этом магазине пусто.", ephemeral=True)
        await i.response.send_message("Выберите товар:", view=LimitsView(key, self), ephemeral=True)

    @discord.ui.button(label="Добавить: игроки", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_players(self, i, b): await i.response.send_modal(ShopAddModal("players", self))

    @discord.ui.button(label="Добавить: персонал", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_staff(self, i, b): await i.response.send_modal(ShopAddModal("staff", self))

    @discord.ui.button(label="Удалить: игроки", emoji="🗑", style=discord.ButtonStyle.danger, row=1)
    async def rem_players(self, i, b): await self._remove(i, "players")

    @discord.ui.button(label="Удалить: персонал", emoji="🗑", style=discord.ButtonStyle.danger, row=1)
    async def rem_staff(self, i, b): await self._remove(i, "staff")

    @discord.ui.button(label="Лимиты: игроки", emoji="⚙️", style=discord.ButtonStyle.primary, row=2)
    async def lim_players(self, i, b): await self._limits(i, "players")

    @discord.ui.button(label="Лимиты: персонал", emoji="⚙️", style=discord.ButtonStyle.primary, row=2)
    async def lim_staff(self, i, b): await self._limits(i, "staff")

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def reload(self, i: discord.Interaction, b):
        await i.response.edit_message(embed=build_admin_embed(), view=self)


class ShopCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="shop", description="Магазин")
    @app_commands.guild_only()
    async def shop_cmd(self, inter: discord.Interaction):
        start = allowed_shops(inter.user)[0]
        view = ShopView(inter, start)
        await inter.response.send_message(embed=build_shop_embed(inter.user, start), view=view, ephemeral=True)

    @app_commands.command(name="orders", description="Ваши заказы из магазина")
    async def orders_cmd(self, inter: discord.Interaction):
        orders = orders_of(inter.user.id)[:10]
        e = discord.Embed(title="🧾 Ваши заказы", color=RED)
        if not orders:
            e.description = "Заказов пока нет."
        else:
            e.description = "\n".join(
                f"**#{o['id']}** {o['name']} — {STATUS[o['status']][0]} · {ts(o['created'], 'd')}"
                + (f"\n└ причина: {o['reason'][:100]}" if o.get("reason") else "")
                for o in orders)
            e.set_footer(text="Показаны последние 10 заказов")
        await inter.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="adm_shop", description="Управление магазинами (куратор)")
    @app_commands.guild_only()
    @app_commands.default_permissions(**PERMS_CURATOR)
    async def adm_shop_cmd(self, inter: discord.Interaction):
        if not is_curator(inter.user):
            return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        view = ShopAdminView(inter)
        await inter.response.send_message(embed=build_admin_embed(), view=view, ephemeral=True)


async def setup(bot):
    bot.add_dynamic_items(OrderButton)
    await bot.add_cog(ShopCog(bot))
