import time
import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import PERMS_CURATOR, ORDER_REMIND_HOURS, ORDER_REMIND_REPEAT_HOURS
from error_reporting import guard
from storage import (
    get_user, set_user, get_shop, save_shop, get_settings,
    create_order, get_order, update_order, delete_order, orders_of, all_orders,
)
from helpers import is_curator, is_staff, send_log

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
MAX_ITEMS_PER_CATEGORY = 25
MAX_QTY_PER_PURCHASE = 100

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


CATEGORIES = {
    "cases": ("🎁", "Кейсы"),
    "privileges": ("👑", "Привилегии"),
    "pokemons": ("🐾", "Покемоны"),
    "items": ("🎒", "Предметы"),
    "staff": ("🛠️", "Staff"),
}
PLAYER_CATEGORIES = ("cases", "privileges", "pokemons", "items")
CATEGORY_ALIASES = {
    "кейсы": "cases", "кейс": "cases", "cases": "cases", "case": "cases",
    "привилегии": "privileges", "привилегия": "privileges", "privileges": "privileges",
    "покемоны": "pokemons", "покемон": "pokemons", "pokemons": "pokemons", "pokemon": "pokemons",
    "предметы": "items", "предмет": "items", "items": "items", "item": "items",
    "staff": "staff", "стафф": "staff", "персонал": "staff",
}


def parse_category(raw):
    return CATEGORY_ALIASES.get(raw.strip().casefold())


def item_category(item):
    if item_shop(item) == "staff":
        return "staff"
    return item.get("category") or "items"


def category_shop(category):
    return "staff" if category == "staff" else "players"


def items_of_category(category):
    return {iid: it for iid, it in get_shop().items() if item_category(it) == category}


def allowed_categories(member):
    if is_staff(member):
        return list(PLAYER_CATEGORIES) + ["staff"] if STAFF_CAN_USE_PLAYER_SHOP else ["staff"]
    return list(PLAYER_CATEGORIES)


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


def order_qty(o):
    return o.get("qty", 1)


def order_total(o):
    return o["price"] * order_qty(o)


def name_with_qty(name, qty):
    return f"{name} × {qty}" if qty > 1 else name


def max_quantity(member, item):
    if item.get("cooldown_days") or item["price"] <= 0:
        return 1 if item.get("cooldown_days") else 0
    have = get_user(member.id)[item_currency(item)]
    limit = min(have // item["price"], MAX_QTY_PER_PURCHASE)
    stock = item.get("stock")
    if stock is not None:
        limit = min(limit, stock)
    return max(limit, 0)


def validate_purchase(member, item_id, item, qty=1):
    if item["price"] <= 0:
        return "❌ У товара некорректная цена."
    if item_shop(item) not in allowed_shops(member):
        return "❌ Этот магазин вам недоступен."
    if qty < 1:
        return "❌ Количество должно быть больше 0."
    if qty > MAX_QTY_PER_PURCHASE:
        return f"❌ За один раз можно купить не больше {MAX_QTY_PER_PURCHASE} шт."
    stock = item.get("stock")
    if stock is not None and stock <= 0:
        return "❌ Товар закончился."
    if stock is not None and stock < qty:
        return f"❌ В наличии только {stock} шт."
    if qty > 1 and item.get("cooldown_days"):
        return "❌ Этот товар можно купить только в одном экземпляре."
    until = cooldown_until(member.id, item_id, item)
    if until:
        return f"❌ Этот товар можно покупать раз в {item['cooldown_days']} дн. Снова будет доступен {ts(until)}."
    cur = item_currency(item)
    have = get_user(member.id)[cur]
    total = item["price"] * qty
    if have < total:
        suffix = f" за {qty} шт" if qty > 1 else ""
        return f"❌ Недостаточно {CURRENCY[cur]['name']}: у вас {num(have)}, нужно {num(total)}{suffix}."
    return None


def _change_stock(item_id, delta):
    shop = get_shop()
    it = shop.get(item_id)
    if it is not None and it.get("stock") is not None:
        it["stock"] = max(0, it["stock"] + delta)
        save_shop(shop)


def build_shop_text(member, category) -> str:
    emoji, label = CATEGORIES[category]
    items = items_of_category(category)
    lines = []
    for n, it in enumerate(items.values(), start=1):
        line = f"**{n}.** {it['name']} — **{price_text(it)}**"
        if limits_text(it):
            line += f" · {limits_text(it)}"
        if it.get("prompt"):
            line += " 📝"
        lines.append(line)
    body = "\n".join(lines) if lines else "Здесь пока пусто."
    u = get_user(member.id)
    text = f"### 🛒 Магазин — {emoji} {label}\n\n{body}\n\n"
    if any(it.get("prompt") for it in items.values()):
        text += "-# 📝 — при покупке нужно уточнить детали\n"
    text += f"-# Ваш баланс: 🪙 {num(u['points'])} · 💵 {num(u['salary'])}"
    return text[:3900]


class ShopCategorySelect(discord.ui.Select):
    def __init__(self, member, owner):
        options = [discord.SelectOption(label=CATEGORIES[k][1], value=k, emoji=CATEGORIES[k][0])
                   for k in allowed_categories(member)]
        super().__init__(placeholder="Выберите категорию", options=options)
        self.owner = owner

    async def callback(self, i: discord.Interaction):
        self.owner.category = self.values[0]
        self.owner.build()
        await i.response.edit_message(view=self.owner)


class ShopItemSelect(discord.ui.Select):
    def __init__(self, items, owner):
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
        super().__init__(placeholder="Выберите товар для покупки…", options=opts[:MAX_ITEMS_PER_CATEGORY])
        self.owner = owner

    async def callback(self, i: discord.Interaction):
        item_id = self.values[0]
        item = get_shop().get(item_id)
        if not item:
            return await i.response.send_message("❌ Этот товар больше недоступен.", ephemeral=True)
        if item_category(item) not in allowed_categories(i.user):
            return await i.response.send_message("❌ Эта категория вам недоступна.", ephemeral=True)
        err = validate_purchase(i.user, item_id, item)
        if err:
            return await i.response.send_message(err, ephemeral=True)
        if item.get("prompt") or max_quantity(i.user, item) > 1:
            await i.response.send_modal(PurchaseModal(item_id, item, i.user, self.owner))
        else:
            await process_purchase(i, item_id, "Не требуется", self.owner)


class ShopView(discord.ui.LayoutView):
    def __init__(self, inter: discord.Interaction, category: str):
        super().__init__(timeout=180)
        self.inter, self.category = inter, category
        self.selects = []
        self.build()

    def build(self):
        self.clear_items()
        self.selects = []
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(build_shop_text(self.inter.user, self.category)))

        category_select = ShopCategorySelect(self.inter.user, self)
        row = discord.ui.ActionRow()
        row.add_item(category_select)
        container.add_item(row)
        self.selects.append(category_select)

        item_select = ShopItemSelect(items_of_category(self.category), self)
        if item_select.options:
            row2 = discord.ui.ActionRow()
            row2.add_item(item_select)
            container.add_item(row2)
            self.selects.append(item_select)
        self.add_item(container)

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.inter.user.id:
            await i.response.send_message("❌ Это меню открыл другой участник. Откройте своё через `/shop`.", ephemeral=True)
            return False
        return True

    async def refresh(self):
        self.build()
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        for sel in self.selects:
            sel.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass


def build_order_embed(o) -> discord.Embed:
    label, color = STATUS[o["status"]]
    cur = CURRENCY[o["currency"]]
    e = discord.Embed(title=f"🛒 Заказ #{o['id']}", color=color, timestamp=discord.utils.utcnow())
    e.add_field(name="Кто", value=f"<@{o['user_id']}>\n`{o['user_id']}`", inline=True)
    e.add_field(name="Что", value=name_with_qty(o["name"], order_qty(o)), inline=True)
    price_text_ = f"{cur['emoji']} {num(order_total(o))}"
    if order_qty(o) > 1:
        price_text_ += f" ({num(o['price'])} × {order_qty(o)})"
    e.add_field(name="Цена", value=price_text_, inline=True)
    e.add_field(name="Магазин", value=SHOPS[o["shop"]]["name"], inline=True)
    e.add_field(name="Статус", value=label, inline=True)
    if o.get("handled_by"):
        e.add_field(name="Обработал", value=f"<@{o['handled_by']}> · {ts(o['handled_at'])}", inline=True)
    e.add_field(name="Детали", value=(o.get("details") or "—")[:1000], inline=False)
    if o.get("reason"):
        e.add_field(name="Причина отказа", value=o["reason"][:500], inline=False)
    if o["status"] == "rejected":
        e.add_field(name="Возврат", value=f"{cur['emoji']} {num(order_total(o))} возвращено покупателю", inline=False)
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
        set_user(o["user_id"], **{o["currency"]: u[o["currency"]] + order_total(o)})
        _change_stock(o["item_id"], +order_qty(o))

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
    e.add_field(name="Товар", value=name_with_qty(o["name"], order_qty(o)), inline=True)
    if o["status"] == "rejected":
        e.add_field(name="Возврат", value=f"{cur['emoji']} {num(order_total(o))} {cur['name']}", inline=True)
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


async def process_purchase(inter: discord.Interaction, item_id: str, details: str, panel=None, qty: int = 1):
    item = get_shop().get(item_id)
    if not item:
        return await _reply(inter, "❌ Этот товар больше недоступен.")
    err = validate_purchase(inter.user, item_id, item, qty)
    if err:
        return await _reply(inter, err)

    ch_id = get_settings().get("purchase_log")
    ch = inter.client.get_channel(ch_id) if ch_id else None
    if ch is None:
        return await _reply(inter, "❌ Магазин временно недоступен: не настроен канал покупок. Сообщите куратору.")

    cur, unit = item_currency(item), item["price"]
    total = unit * qty
    u = get_user(inter.user.id)
    set_user(inter.user.id, **{cur: u[cur] - total})
    _change_stock(item_id, -qty)
    oid = create_order(user_id=inter.user.id, item_id=item_id, name=item["name"], price=unit, qty=qty,
                       currency=cur, shop=item_shop(item), details=details)
    try:
        msg = await ch.send(embed=build_order_embed(get_order(oid)), view=order_view(oid, "new"))
    except discord.HTTPException:
        u = get_user(inter.user.id)
        set_user(inter.user.id, **{cur: u[cur] + total})
        _change_stock(item_id, +qty)
        delete_order(oid)
        return await _reply(inter, "❌ Не удалось оформить заказ (бот не может писать в канал покупок). Средства не списаны.")
    update_order(oid, channel_id=ch.id, message_id=msg.id)

    price_line = f"{CURRENCY[cur]['emoji']} {num(total)}"
    if qty > 1:
        price_line += f" ({num(unit)} × {qty})"
    await _reply(inter, f"✅ Куплено: **{name_with_qty(item['name'], qty)}** за {price_line}\n"
                        f"🧾 Заказ **#{oid}**\n📝 {details}\n"
                        "О статусе заказа придёт уведомление в личные сообщения, список заказов: `/orders`.")
    if panel:
        await panel.refresh()


class PurchaseModal(discord.ui.Modal):
    def __init__(self, item_id, item, member, panel):
        super().__init__(title="Оформление заказа")
        self.item_id, self.panel = item_id, panel
        self.qty = None
        self.det = None
        limit = max_quantity(member, item)
        if limit > 1:
            self.qty = discord.ui.TextInput(label=f"Количество (макс. {limit})"[:45], default="1", max_length=4)
            self.add_item(self.qty)
        if item.get("prompt"):
            self.det = discord.ui.TextInput(label=item["prompt"][:45], style=discord.TextStyle.paragraph,
                                            required=True, max_length=300)
            self.add_item(self.det)

    async def on_submit(self, inter: discord.Interaction):
        qty = 1
        if self.qty is not None:
            try:
                qty = int(self.qty.value.strip())
            except ValueError:
                return await inter.response.send_message("❌ Количество должно быть целым числом.", ephemeral=True)
        details = self.det.value if self.det is not None else "Не требуется"
        await process_purchase(inter, self.item_id, details, self.panel, qty)


def build_admin_embed() -> discord.Embed:
    e = discord.Embed(
        title="🛠️ Управление магазином",
        description="Добавляйте, изменяйте, удаляйте товары и настраивайте лимиты.\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        color=RED, timestamp=discord.utils.utcnow())
    for key, (emoji, label) in CATEGORIES.items():
        items = items_of_category(key)
        lines = []
        for it in items.values():
            line = f"• {it['name']} — {price_text(it)}"
            if limits_text(it):
                line += f" ({limits_text(it)})"
            lines.append(line)
        e.add_field(name=f"{emoji} {label} ({len(items)}/{MAX_ITEMS_PER_CATEGORY})",
                    value=("\n".join(lines) or "`пусто`")[:1024], inline=True)
    return e


class CuratorView(discord.ui.View):
    async def interaction_check(self, i: discord.Interaction) -> bool:
        if not is_curator(i.user):
            await i.response.send_message("❌ Нет прав.", ephemeral=True)
            return False
        return True


class AdminCategorySelect(discord.ui.Select):
    def __init__(self, handler):
        options = [discord.SelectOption(label=label, value=key, emoji=emoji,
                                        description=f"Товаров: {len(items_of_category(key))}")
                   for key, (emoji, label) in CATEGORIES.items()]
        super().__init__(placeholder="Выберите категорию", options=options)
        self.handler = handler

    async def callback(self, i: discord.Interaction):
        await self.handler(i, self.values[0])


class AdminCategoryView(CuratorView):
    def __init__(self, handler):
        super().__init__(timeout=60)
        self.add_item(AdminCategorySelect(handler))


class AdminItemSelect(discord.ui.Select):
    def __init__(self, category, handler, placeholder):
        opts = []
        for iid, it in list(items_of_category(category).items())[:MAX_ITEMS_PER_CATEGORY]:
            desc = price_text(it)
            if limits_text(it):
                desc += f" · {limits_text(it)}"
            opts.append(discord.SelectOption(label=it["name"][:80], value=iid, description=desc[:100]))
        super().__init__(placeholder=placeholder, options=opts)
        self.handler = handler

    async def callback(self, i: discord.Interaction):
        await self.handler(i, self.values[0])


class AdminItemView(CuratorView):
    def __init__(self, category, handler, placeholder):
        super().__init__(timeout=60)
        self.add_item(AdminItemSelect(category, handler, placeholder))


class ShopAddModal(discord.ui.Modal):
    def __init__(self, category, panel):
        emoji, label = CATEGORIES[category]
        super().__init__(title=f"Добавить: {label}"[:45])
        self.category, self.panel = category, panel
        self.shop_key = category_shop(category)
        self.s_name = discord.ui.TextInput(label="Название", max_length=100)
        self.s_price = discord.ui.TextInput(label="Цена", max_length=10)
        default = "баллы" if DEFAULT_CURRENCY[self.shop_key] == "points" else "зп"
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

        if len(items_of_category(self.category)) >= MAX_ITEMS_PER_CATEGORY:
            return await inter.response.send_message(
                f"❌ В категории уже {MAX_ITEMS_PER_CATEGORY} товаров, это максимум.", ephemeral=True)

        shop = get_shop()
        shop[str(time.time_ns())] = {
            "name": self.s_name.value, "price": price, "prompt": self.s_prompt.value or None,
            "shop": self.shop_key, "category": self.category, "currency": currency,
            "stock": None, "cooldown_days": None,
        }
        save_shop(shop)
        emoji, label = CATEGORIES[self.category]
        await inter.response.send_message(
            f"✅ **{self.s_name.value}** добавлен в «{emoji} {label}» — "
            f"{CURRENCY[currency]['emoji']} {num(price)}. Лимиты задаются кнопкой «Лимиты».", ephemeral=True)
        await self.panel.refresh()


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


class EditModal(discord.ui.Modal):
    def __init__(self, item_id, item, panel):
        super().__init__(title=f"Изменить: {item['name']}"[:45])
        self.item_id, self.panel = item_id, panel
        cur_name = "баллы" if item_currency(item) == "points" else "зп"
        self.s_name = discord.ui.TextInput(label="Название", max_length=100, default=item["name"][:100])
        self.s_price = discord.ui.TextInput(label="Цена", max_length=10, default=str(item["price"]))
        self.s_cur = discord.ui.TextInput(label="Валюта: баллы или зп", required=False, max_length=10, default=cur_name)
        self.s_cat = discord.ui.TextInput(label="Категория", required=False, max_length=20,
                                          default=CATEGORIES[item_category(item)][1],
                                          placeholder="кейсы, привилегии, покемоны, предметы, staff")
        self.s_prompt = discord.ui.TextInput(label="Вопрос при покупке (пусто = убрать)", required=False,
                                             max_length=100, default=item.get("prompt") or None)
        for f in (self.s_name, self.s_price, self.s_cur, self.s_cat, self.s_prompt):
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
        name = self.s_name.value.strip()
        if not name:
            return await inter.response.send_message("❌ Название не может быть пустым.", ephemeral=True)
        raw = self.s_cur.value.strip().lower()
        if raw and raw not in CUR_ALIASES:
            return await inter.response.send_message("❌ Валюта: «баллы» или «зп».", ephemeral=True)

        shop = get_shop()
        it = shop.get(self.item_id)
        if it is None:
            return await inter.response.send_message("❌ Товар уже удалён.", ephemeral=True)

        new_cat = item_category(it)
        if self.s_cat.value.strip():
            new_cat = parse_category(self.s_cat.value)
            if new_cat is None:
                return await inter.response.send_message(
                    "❌ Категория: кейсы, привилегии, покемоны, предметы или staff.", ephemeral=True)
        if new_cat != item_category(it) and len(items_of_category(new_cat)) >= MAX_ITEMS_PER_CATEGORY:
            return await inter.response.send_message(
                f"❌ В категории «{CATEGORIES[new_cat][1]}» уже {MAX_ITEMS_PER_CATEGORY} товаров.", ephemeral=True)

        old = dict(it)
        it["name"] = name
        it["price"] = price
        if raw:
            it["currency"] = CUR_ALIASES[raw]
        it["category"] = new_cat
        it["shop"] = category_shop(new_cat)
        it["prompt"] = self.s_prompt.value.strip() or None
        save_shop(shop)

        changes = []
        if old["name"] != it["name"]:
            changes.append(f"Название: {old['name']} → {it['name']}")
        if old["price"] != it["price"] or item_currency(old) != item_currency(it):
            changes.append(f"Цена: {price_text(old)} → {price_text(it)}")
        if item_category(old) != new_cat:
            changes.append(f"Категория: {CATEGORIES[item_category(old)][1]} → {CATEGORIES[new_cat][1]}")
        if (old.get("prompt") or None) != it["prompt"]:
            changes.append("Вопрос при покупке: " + ("изменён" if it["prompt"] else "убран"))
        text = "\n".join(f"• {c}" for c in changes) or "Изменений нет."
        await inter.response.send_message(
            f"✅ Товар обновлён:\n{text}\nУже оформленные заказы остаются с прежней ценой.", ephemeral=True)

        if changes:
            e = discord.Embed(title="✏️ Товар изменён", color=RED, timestamp=discord.utils.utcnow())
            e.add_field(name="Куратор", value=inter.user.mention, inline=True)
            e.add_field(name="Категория", value=CATEGORIES[new_cat][1], inline=True)
            e.add_field(name="Изменения", value=text[:1000], inline=False)
            await send_log(inter.client, "pay_log", e)
        await self.panel.refresh()


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

    async def _pick_item(self, i: discord.Interaction, category, handler, placeholder):
        if not items_of_category(category):
            return await i.response.edit_message(content="В этой категории пусто.", view=None)
        await i.response.edit_message(content="Выберите товар:", view=AdminItemView(category, handler, placeholder))

    async def _add_to(self, i: discord.Interaction, category):
        await i.response.send_modal(ShopAddModal(category, self))

    async def _edit_cat(self, i: discord.Interaction, category):
        await self._pick_item(i, category, self._edit_item, "Какой товар изменить?")

    async def _edit_item(self, i: discord.Interaction, item_id):
        item = get_shop().get(item_id)
        if not item:
            return await i.response.edit_message(content="❌ Товар уже удалён.", view=None)
        await i.response.send_modal(EditModal(item_id, item, self))

    async def _remove_cat(self, i: discord.Interaction, category):
        await self._pick_item(i, category, self._remove_item, "Что удалить?")

    async def _remove_item(self, i: discord.Interaction, item_id):
        shop = get_shop()
        item = shop.pop(item_id, None)
        save_shop(shop)
        name = item["name"] if item else "Товар"
        await i.response.edit_message(content=f"✅ **{name}** удалён.", view=None)
        await self.refresh()

    async def _limits_cat(self, i: discord.Interaction, category):
        await self._pick_item(i, category, self._limits_item, "Для какого товара настроить лимиты?")

    async def _limits_item(self, i: discord.Interaction, item_id):
        item = get_shop().get(item_id)
        if not item:
            return await i.response.edit_message(content="❌ Товар уже удалён.", view=None)
        await i.response.send_modal(LimitsModal(item_id, item, self))

    @discord.ui.button(label="Добавить", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_btn(self, i, b):
        await i.response.send_message("В какую категорию добавить товар?", view=AdminCategoryView(self._add_to), ephemeral=True)

    @discord.ui.button(label="Изменить", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit_btn(self, i, b):
        await i.response.send_message("В какой категории товар?", view=AdminCategoryView(self._edit_cat), ephemeral=True)

    @discord.ui.button(label="Удалить", emoji="🗑", style=discord.ButtonStyle.danger, row=0)
    async def remove_btn(self, i, b):
        await i.response.send_message("В какой категории товар?", view=AdminCategoryView(self._remove_cat), ephemeral=True)

    @discord.ui.button(label="Лимиты", emoji="⚙️", style=discord.ButtonStyle.primary, row=1)
    async def limits_btn(self, i, b):
        await i.response.send_message("В какой категории товар?", view=AdminCategoryView(self._limits_cat), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def reload(self, i: discord.Interaction, b):
        await i.response.edit_message(embed=build_admin_embed(), view=self)


class ShopCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    async def cog_load(self):
        self.order_reminders.start()

    def cog_unload(self):
        self.order_reminders.cancel()

    @tasks.loop(minutes=30)
    @guard("order_reminders")
    async def order_reminders(self):
        now = time.time()
        stale = [o for o in all_orders()
                 if o["status"] == "new"
                 and now - o["created"] >= ORDER_REMIND_HOURS * 3600
                 and now - o.get("last_remind", 0) >= ORDER_REMIND_REPEAT_HOURS * 3600]
        if not stale:
            return
        s = get_settings()
        channel = self.bot.get_channel(s.get("purchase_log") or 0)
        if channel is None:
            return
        stale.sort(key=lambda o: o["created"])
        lines = []
        for o in stale[:15]:
            hours = int((now - o["created"]) // 3600)
            link = ""
            if o.get("channel_id") and o.get("message_id"):
                link = f" · [открыть](https://discord.com/channels/{channel.guild.id}/{o['channel_id']}/{o['message_id']})"
            lines.append(f"**#{o['id']}** {name_with_qty(o['name'], order_qty(o))} · <@{o['user_id']}> · ждёт {hours} ч.{link}")
        if len(stale) > 15:
            lines.append(f"… и ещё {len(stale) - 15}")
        e = discord.Embed(title="⏰ Необработанные заказы", description="\n".join(lines), color=ORANGE,
                          timestamp=discord.utils.utcnow())
        e.set_footer(text="Нажмите «Принять» или «Отклонить» под заказом в этом канале")
        role = channel.guild.get_role(s.get("curator_role") or 0)
        try:
            await channel.send(content=role.mention if role else None, embed=e,
                               allowed_mentions=discord.AllowedMentions(roles=True))
        except discord.HTTPException:
            return
        for o in stale:
            update_order(o["id"], last_remind=now)

    @order_reminders.before_loop
    async def _before_reminders(self): await self.bot.wait_until_ready()

    @app_commands.command(name="shop", description="Магазин")
    @app_commands.guild_only()
    async def shop_cmd(self, inter: discord.Interaction):
        view = ShopView(inter, allowed_categories(inter.user)[0])
        await inter.response.send_message(view=view, ephemeral=True)

    @app_commands.command(name="orders", description="Ваши заказы из магазина")
    async def orders_cmd(self, inter: discord.Interaction):
        orders = orders_of(inter.user.id)[:10]
        e = discord.Embed(title="🧾 Ваши заказы", color=RED)
        if not orders:
            e.description = "Заказов пока нет."
        else:
            e.description = "\n".join(
                f"**#{o['id']}** {name_with_qty(o['name'], order_qty(o))} — {STATUS[o['status']][0]} · {ts(o['created'], 'd')}"
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
