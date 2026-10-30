import discord
from discord.ext import commands
from discord import app_commands
from storage import (
    get_settings, set_setting, get_staff, set_staff, del_staff, all_users, get_shop
)
from helpers import is_owner
from config import OWNER_ID, PERMS_OWNER

RED = 0x9B111E
LEVELS = {
    "curator":     ("👑", "Куратор"),
    "responsible": ("🛡️", "Ответственный"),
}

# (ключ настройки, полное название, короткое для кнопки, эмоджи)
CHANNELS = [
    ("purchase_log", "Канал покупок",   "Покупки",   "📢"),
    ("mod_log",      "Канал модерации", "Модерация", "⚖️"),
    ("pay_log",      "Канал переводов", "Переводы",  "💸"),
    ("message_log",  "Канал сообщений", "Сообщения", "💬"),
    ("voice_log",    "Канал войса",     "Войс",      "🎙️"),
]
ROLES = [
    ("builder_role", "Роль строителей", "Строители", "🏗️"),
    ("tester_role",  "Роль тестеров",   "Тестеры",   "🧪"),
    # Discord-роли должностей: выдаются автоматически при назначении и дают видимость команд
    ("curator_role",     "Роль кураторов",      "Кураторы",      "👑"),
    ("responsible_role", "Роль ответственных",  "Ответственные", "🛡️"),
]


# ═══════════════ ПЛАШКА ═══════════════
def _chan(guild, cid):
    if not cid:
        return "`не задан`"
    return f"<#{cid}>" if guild.get_channel(cid) else "⚠️ `удалён`"


def _role(guild, rid):
    if not rid:
        return "`не задана`"
    return f"<@&{rid}>" if guild.get_role(rid) else "⚠️ `удалена`"


def _staff_list(level):
    ids = [int(uid) for uid, lvl in get_staff().items() if lvl == level]
    if not ids:
        return "`никого`"
    shown = "\n".join(f"• <@{u}>" for u in ids[:10])
    if len(ids) > 10:
        shown += f"\n… и ещё {len(ids) - 10}"
    return shown


def build_embed(guild: discord.Guild, user: discord.abc.User) -> discord.Embed:
    s = get_settings()
    users = all_users()
    total_salary = sum(u.get("salary", 0) for u in users.values())

    e = discord.Embed(
        title="🎛️ Панель управления",
        description=(
            "Доступна **только владельцу**.\n"
            "Каналы логов, роли веток и должности персонала.\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
        ),
        color=RED,
        timestamp=discord.utils.utcnow(),
    )
    e.set_author(name="Ashra Team • Discord Server",
                 icon_url=guild.icon.url if guild.icon else None)
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)

    e.add_field(
        name="📡 Каналы логов",
        value="\n".join(f"{emoji} **{short}:** {_chan(guild, s[key])}"
                        for key, _, short, emoji in CHANNELS),
        inline=True)
    e.add_field(
        name="🎭 Роли веток",
        value="\n".join(f"{emoji} **{short}:** {_role(guild, s[key])}"
                        for key, _, short, emoji in ROLES),
        inline=True)
    e.add_field(
        name="📊 Данные",
        value=f"**Участников:** `{len(users)}`\n"
              f"**Товаров:** `{len(get_shop())}`\n"
              f"**ЗП на руках:** `{total_salary:,}`".replace(",", " "),
        inline=True)
    e.add_field(name="👑 Кураторы", value=_staff_list("curator"), inline=True)
    e.add_field(name="🛡️ Ответственные", value=_staff_list("responsible"), inline=True)
    e.add_field(name="🔑 Владелец", value=f"<@{OWNER_ID}>", inline=True)

    e.set_footer(text=f"Открыл: {user.display_name}", icon_url=user.display_avatar.url)
    return e


# ═══════════════ КОМАНДА ═══════════════
class AdminPanelCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="admin", description="Панель управления ботом (только владелец)")
    @app_commands.guild_only()
    @app_commands.default_permissions(**PERMS_OWNER)
    async def admin_cmd(self, inter: discord.Interaction):
        if not is_owner(inter.user):
            return await inter.response.send_message("❌ Только владелец.", ephemeral=True)
        view = AdminView(inter)
        await inter.response.send_message(embed=build_embed(inter.guild, inter.user), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminPanelCog(bot))


# ═══════════════ БАЗА: только владелец ═══════════════
class OwnerView(discord.ui.View):
    async def interaction_check(self, i: discord.Interaction) -> bool:
        if not is_owner(i.user):
            await i.response.send_message("❌ Только владелец.", ephemeral=True)
            return False
        return True


# ═══════════════ ВЫБОР КАНАЛА / РОЛИ ═══════════════
class ChannelPick(OwnerView):
    def __init__(self, key, title, panel):
        super().__init__(timeout=60)
        self.key, self.title, self.panel = key, title, panel

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text],
                       placeholder="Выберите текстовый канал")
    async def pick(self, i: discord.Interaction, sel):
        ch = i.guild.get_channel(sel.values[0].id)
        if ch is None:
            return await i.response.edit_message(content="❌ Канал не найден.", view=None)
        perms = ch.permissions_for(i.guild.me)
        missing = [n for n, ok in (("Просмотр канала", perms.view_channel),
                                   ("Отправка сообщений", perms.send_messages),
                                   ("Встраивание ссылок", perms.embed_links)) if not ok]
        if missing:
            return await i.response.edit_message(
                content=f"❌ У бота нет прав в {ch.mention}: {', '.join(missing)}.", view=None)
        set_setting(self.key, ch.id)
        await i.response.edit_message(content=f"✅ **{self.title}:** {ch.mention}", view=None)
        self.stop()
        await self.panel.refresh()

    @discord.ui.button(label="Отвязать", style=discord.ButtonStyle.danger, emoji="🧹")
    async def clear(self, i: discord.Interaction, b):
        set_setting(self.key, None)
        await i.response.edit_message(content=f"🧹 **{self.title}** отвязан.", view=None)
        self.stop()
        await self.panel.refresh()


class RolePick(OwnerView):
    def __init__(self, key, title, panel):
        super().__init__(timeout=60)
        self.key, self.title, self.panel = key, title, panel

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Выберите роль")
    async def pick(self, i: discord.Interaction, sel):
        role = sel.values[0]
        if role.is_default():
            return await i.response.edit_message(content="❌ Нельзя выбрать @everyone.", view=None)
        set_setting(self.key, role.id)
        await i.response.edit_message(content=f"✅ **{self.title}:** {role.mention}", view=None)
        self.stop()
        await self.panel.refresh()

    @discord.ui.button(label="Отвязать", style=discord.ButtonStyle.danger, emoji="🧹")
    async def clear(self, i: discord.Interaction, b):
        set_setting(self.key, None)
        await i.response.edit_message(content=f"🧹 **{self.title}** отвязана.", view=None)
        self.stop()
        await self.panel.refresh()


# ═══════════════ ВЫДАЧА / СНЯТИЕ ДОЛЖНОСТЕЙ ═══════════════
async def sync_staff_role(guild: discord.Guild, user, new_level):
    """Выдаёт Discord-роль должности и снимает ненужную. Возвращает примечание для ответа."""
    s = get_settings()
    mapping = {"curator": s.get("curator_role"), "responsible": s.get("responsible_role")}
    member = guild.get_member(user.id)
    if member is None:
        return "\n⚠️ Участник не найден на сервере, Discord-роль не выдана."

    notes, problem, configured = [], False, False
    for level, rid in mapping.items():
        role = guild.get_role(rid) if rid else None
        if role is None:
            continue
        configured = True
        try:
            if level == new_level and role not in member.roles:
                await member.add_roles(role, reason="Должность из /admin")
            elif level != new_level and role in member.roles:
                await member.remove_roles(role, reason="Должность из /admin")
        except discord.HTTPException:
            problem = True
    if problem:
        notes.append("⚠️ Discord-роль выдать не удалось: проверьте право «Управление ролями» "
                     "и что роль бота выше ролей должностей.")
    if new_level and not configured:
        notes.append("ℹ️ Discord-роли должностей не привязаны в панели: команды в списке "
                     "у участника не появятся.")
    return ("\n" + "\n".join(notes)) if notes else ""


class GrantView(OwnerView):
    def __init__(self, level, panel):
        super().__init__(timeout=60)
        self.level, self.panel = level, panel

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Выберите участника")
    async def pick(self, i: discord.Interaction, sel):
        target = sel.values[0]
        if target.bot:
            return await i.response.edit_message(content="❌ Боту нельзя выдать должность.", view=None)
        if is_owner(target):
            return await i.response.edit_message(content="❌ У владельца и так полный доступ.", view=None)

        current = get_staff().get(str(target.id))
        if self.level is None:
            if not current:
                return await i.response.edit_message(
                    content=f"ℹ️ У {target.mention} нет должности.", view=None)
            del_staff(target.id)
            msg = f"✅ С {target.mention} снята должность: **{LEVELS[current][1]}**"
        else:
            emoji, name = LEVELS[self.level]
            if current == self.level:
                return await i.response.edit_message(
                    content=f"ℹ️ {target.mention} уже **{name}**.", view=None)
            set_staff(target.id, self.level)
            was = f" (было: {LEVELS[current][1]})" if current in LEVELS else ""
            msg = f"✅ {target.mention} → {emoji} **{name}**{was}"
        await i.response.defer()
        note = await sync_staff_role(i.guild, target, self.level)
        await i.edit_original_response(content=msg + note, view=None)
        self.stop()
        await self.panel.refresh()


# ═══════════════ ГЛАВНАЯ ПАНЕЛЬ ═══════════════
class SettingButton(discord.ui.Button):
    def __init__(self, key, title, short, emoji, kind, row):
        super().__init__(label=short, emoji=emoji, row=row,
                         style=discord.ButtonStyle.primary if kind == "channel" else discord.ButtonStyle.success)
        self.key, self.title, self.kind = key, title, kind

    async def callback(self, i: discord.Interaction):
        cls = ChannelPick if self.kind == "channel" else RolePick
        what = "канал" if self.kind == "channel" else "роль"
        await i.response.send_message(f"**{self.title}** — выберите {what}:",
                                      view=cls(self.key, self.title, self.view), ephemeral=True)


class AdminView(OwnerView):
    def __init__(self, inter: discord.Interaction):
        super().__init__(timeout=600)
        self.inter = inter
        for n, (key, title, short, emoji) in enumerate(CHANNELS):
            self.add_item(SettingButton(key, title, short, emoji, "channel", row=0 if n < 3 else 1))
        for key, title, short, emoji in ROLES:
            self.add_item(SettingButton(key, title, short, emoji, "role", row=2))

    async def refresh(self):
        try:
            await self.inter.edit_original_response(
                embed=build_embed(self.inter.guild, self.inter.user), view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Куратор", emoji="👑", style=discord.ButtonStyle.secondary, row=3)
    async def g1(self, i, b):
        await i.response.send_message("Кого назначить куратором?", view=GrantView("curator", self), ephemeral=True)

    @discord.ui.button(label="Ответственный", emoji="🛡️", style=discord.ButtonStyle.secondary, row=3)
    async def g2(self, i, b):
        await i.response.send_message("Кого назначить ответственным?", view=GrantView("responsible", self), ephemeral=True)

    @discord.ui.button(label="Снять должность", emoji="❌", style=discord.ButtonStyle.danger, row=3)
    async def g3(self, i, b):
        await i.response.send_message("С кого снять должность?", view=GrantView(None, self), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=4)
    async def reload(self, i: discord.Interaction, b):
        await i.response.edit_message(embed=build_embed(i.guild, i.user), view=self)
