import discord
from discord.ext import commands
from discord import app_commands
from storage import (
    get_settings, set_setting, get_staff, staff_levels, add_staff_level, remove_staff_level, all_users, get_shop
)
from helpers import is_owner, member_levels
from config import OWNER_ID, PERMS_OWNER, PERMS_CURATOR, PERMS_RESPONSIBLE

RED = 0x9B111E
LEVELS = {
    "curator":     ("👑", "Куратор"),
    "responsible": ("🛡️", "Ответственный"),
}

CHANNELS = [
    ("purchase_log", "Канал покупок",   "Покупки",   "📢"),
    ("mod_log",      "Канал модерации", "Модерация", "⚖️"),
    ("pay_log",      "Канал переводов", "Переводы",  "💸"),
    ("message_log",  "Канал сообщений", "Сообщения", "💬"),
    ("voice_log",    "Канал войса",     "Войс",      "🎙️"),
    ("payout_builder_channel", "Канал выплат строителей", "Выплаты: строители", "🏗️"),
    ("payout_tester_channel",  "Канал выплат тестеров",   "Выплаты: тестеры",  "🧪"),
]
ROLES = [
    ("builder_role", "Роль строителей", "Строители", "🏗️"),
    ("tester_role",  "Роль тестеров",   "Тестеры",   "🧪"),
    ("curator_role",     "Роль кураторов",      "Кураторы",      "👑"),
    ("responsible_role", "Роль ответственных",  "Ответственные", "🛡️"),
]


def _chan(guild, cid):
    if not cid:
        return "`не задан`"
    return f"<#{cid}>" if guild.get_channel(cid) else "⚠️ `удалён`"


def _role(guild, rid):
    if not rid:
        return "`не задана`"
    return f"<@&{rid}>" if guild.get_role(rid) else "⚠️ `удалена`"


def _staff_list(guild, level):
    ids = {int(uid) for uid in get_staff() if level in staff_levels(uid)}
    rid = get_settings().get(f"{level}_role")
    role = guild.get_role(rid) if rid else None
    if role:
        ids.update(m.id for m in role.members)
    ids = sorted(ids)
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
        name="📡 Каналы",
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
    e.add_field(name="👑 Кураторы", value=_staff_list(guild, "curator"), inline=True)
    e.add_field(name="🛡️ Ответственные", value=_staff_list(guild, "responsible"), inline=True)
    e.add_field(name="🔑 Владелец", value=f"<@{OWNER_ID}>", inline=True)

    e.set_footer(text=f"Открыл: {user.display_name}", icon_url=user.display_avatar.url)
    return e


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


class OwnerView(discord.ui.View):
    async def interaction_check(self, i: discord.Interaction) -> bool:
        if not is_owner(i.user):
            await i.response.send_message("❌ Только владелец.", ephemeral=True)
            return False
        return True


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


ROLE_RIGHTS = {
    "curator_role": {**PERMS_CURATOR, **PERMS_RESPONSIBLE},
    "responsible_role": dict(PERMS_RESPONSIBLE),
}
RIGHT_NAMES = {"manage_messages": "Управление сообщениями", "moderate_members": "Модерация участников"}


async def ensure_role_rights(role, key):
    need = [n for n, v in ROLE_RIGHTS.get(key, {}).items() if v and not getattr(role.permissions, n)]
    if not need:
        return ""
    names = ", ".join(RIGHT_NAMES.get(n, n) for n in need)
    perms = discord.Permissions(role.permissions.value)
    perms.update(**{n: True for n in need})
    try:
        await role.edit(permissions=perms, reason="Права для отображения команд бота")
    except discord.HTTPException:
        return f"\n⚠️ Не удалось добавить роли права ({names}): включите их вручную, иначе команды не появятся."
    return f"\n🔧 Роли добавлены права: {names}."


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
        await i.response.defer()
        note = await ensure_role_rights(role, self.key)
        await i.edit_original_response(content=f"✅ **{self.title}:** {role.mention}{note}", view=None)
        self.stop()
        await self.panel.refresh()

    @discord.ui.button(label="Отвязать", style=discord.ButtonStyle.danger, emoji="🧹")
    async def clear(self, i: discord.Interaction, b):
        set_setting(self.key, None)
        await i.response.edit_message(content=f"🧹 **{self.title}** отвязана.", view=None)
        self.stop()
        await self.panel.refresh()


async def sync_staff_role(guild, user, add=(), remove=()):
    member = guild.get_member(user.id)
    if member is None:
        return "\n⚠️ Участник не найден на сервере, Discord-роли не изменены."
    s = get_settings()
    problem, unbound = False, []
    for level in add:
        role = guild.get_role(s.get(f"{level}_role") or 0)
        if role is None:
            unbound.append(LEVELS[level][1])
            continue
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Должность из /admin")
            except discord.HTTPException:
                problem = True
    for level in remove:
        role = guild.get_role(s.get(f"{level}_role") or 0)
        if role is not None and role in member.roles:
            try:
                await member.remove_roles(role, reason="Должность из /admin")
            except discord.HTTPException:
                problem = True
    notes = []
    if problem:
        notes.append("⚠️ Discord-роль изменить не удалось: проверьте право «Управление ролями» "
                     "и что роль бота выше ролей должностей.")
    if unbound:
        notes.append("ℹ️ Не привязана Discord-роль: " + ", ".join(unbound) +
                     ". Без неё команды в списке не появятся.")
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

        member = i.guild.get_member(target.id)
        levels = member_levels(member) if member else set(staff_levels(target.id))
        emoji, name = LEVELS[self.level]
        if self.level in levels:
            return await i.response.edit_message(content=f"ℹ️ {target.mention} уже **{name}**.", view=None)

        add_staff_level(target.id, self.level)
        await i.response.defer()
        note = await sync_staff_role(i.guild, target, add=[self.level])
        now = ", ".join(LEVELS[lv][1] for lv in LEVELS if lv in levels | {self.level})
        await i.edit_original_response(
            content=f"✅ {target.mention} → {emoji} **{name}** (сейчас: {now}){note}", view=None)
        self.stop()
        await self.panel.refresh()


class RevokePick(OwnerView):
    def __init__(self, target, levels, panel):
        super().__init__(timeout=60)
        self.target, self.levels, self.panel = target, levels, panel
        opts = [discord.SelectOption(label=LEVELS[lv][1], value=lv, emoji=LEVELS[lv][0]) for lv in levels]
        if len(levels) > 1:
            opts.append(discord.SelectOption(label="Все должности", value="all", emoji="🧹"))
        self.sel = discord.ui.Select(placeholder="Какую должность снять?", options=opts)
        self.sel.callback = self.picked
        self.add_item(self.sel)

    async def picked(self, i: discord.Interaction):
        choice = self.sel.values[0]
        todo = list(self.levels) if choice == "all" else [choice]
        for lv in todo:
            remove_staff_level(self.target.id, lv)
        await i.response.defer()
        note = await sync_staff_role(i.guild, self.target, remove=todo)
        names = ", ".join(LEVELS[lv][1] for lv in todo)
        await i.edit_original_response(content=f"✅ С {self.target.mention} снято: **{names}**{note}", view=None)
        self.stop()
        await self.panel.refresh()


class RevokeView(OwnerView):
    def __init__(self, panel):
        super().__init__(timeout=60)
        self.panel = panel

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Выберите участника")
    async def pick(self, i: discord.Interaction, sel):
        target = sel.values[0]
        member = i.guild.get_member(target.id)
        levels = sorted(member_levels(member) if member else set(staff_levels(target.id)))
        if not levels:
            return await i.response.edit_message(content=f"ℹ️ У {target.mention} нет должности.", view=None)
        await i.response.edit_message(content=f"Какую должность снять у {target.mention}?",
                                      view=RevokePick(target, levels, self.panel))


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
        await i.response.send_message("С кого снять должность?", view=RevokeView(self), ephemeral=True)

    @discord.ui.button(label="Обновить", emoji="🔄", style=discord.ButtonStyle.secondary, row=4)
    async def reload(self, i: discord.Interaction, b):
        await i.response.edit_message(embed=build_embed(i.guild, i.user), view=self)
