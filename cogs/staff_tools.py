import time
import discord
from discord.ext import commands
from discord import app_commands
from config import PERMS_CURATOR
from storage import get_user, get_settings
from helpers import is_curator

RED = 0x9B111E
BRANCHES = {"all": "Все", "builder": "Строители", "tester": "Тестеры"}
BRANCH_EMOJI = {"builder": "🏗️", "tester": "🧪"}


def ago(t) -> str:
    return f"<t:{int(t)}:R>" if t else "—"


def fit(lines, limit=1000):
    out, size = [], 0
    for n, line in enumerate(lines):
        if size + len(line) + 1 > limit:
            out.append(f"… и ещё {len(lines) - n}")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out) or "—"


def build_activity(guild: discord.Guild, branch: str, days: int) -> discord.Embed:
    s = get_settings()
    roles = {k: guild.get_role(s.get(f"{k}_role")) for k in ("builder", "tester") if s.get(f"{k}_role")}
    roles = {k: r for k, r in roles.items() if r}

    e = discord.Embed(title="📈 Активность персонала", color=RED, timestamp=discord.utils.utcnow())
    if not roles:
        e.description = "Роли строителей и тестеров не настроены (их задаёт владелец в `/admin`)."
        return e

    members = {}
    for key, role in roles.items():
        if branch in ("all", key):
            for m in role.members:
                if not m.bot:
                    members.setdefault(m.id, (m, []))[1].append(key)

    now = time.time()
    inactive, active, nodata = [], [], []
    for m, keys in members.values():
        u = get_user(m.id)
        last_msg, last_voice = u["last_msg"], u["last_voice"]
        last = max(last_msg, last_voice)
        tag = "".join(BRANCH_EMOJI[k] for k in keys)
        text = f"{tag} {m.mention} — сообщ.: {ago(last_msg)} · войс: {ago(last_voice)}"
        if last == 0:
            nodata.append((0, text))
        elif now - last >= days * 86400:
            inactive.append((last, text))
        else:
            active.append((last, text))

    inactive.sort(key=lambda x: x[0])
    active.sort(key=lambda x: x[0], reverse=True)

    e.description = (f"Ветка: **{BRANCHES[branch]}** · неактивны от **{days}** дн.\n"
                     f"🔴 {len(inactive)} · 🟢 {len(active)} · ⚪ {len(nodata)}\n"
                     "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬")
    if not members:
        e.description += "\nВ этой ветке никого нет."
        return e
    if inactive:
        e.add_field(name=f"🔴 Давно молчат ({days}+ дн.)", value=fit([t for _, t in inactive]), inline=False)
    if active:
        e.add_field(name="🟢 Активны", value=fit([t for _, t in active]), inline=False)
    if nodata:
        e.add_field(name="⚪ Пока нет данных", value=fit([t for _, t in nodata]), inline=False)
    e.set_footer(text="🏗️ строитель · 🧪 тестер · данные копятся с момента добавления функции")
    return e


class BranchSelect(discord.ui.Select):
    def __init__(self, current):
        super().__init__(placeholder="Ветка", options=[
            discord.SelectOption(label=lbl, value=k, default=(k == current)) for k, lbl in BRANCHES.items()])

    async def callback(self, i: discord.Interaction):
        v: ActivityView = self.view
        v.branch = self.values[0]
        v.rebuild()
        await i.response.edit_message(embed=build_activity(i.guild, v.branch, v.days), view=v)


class ActivityView(discord.ui.View):
    def __init__(self, inter: discord.Interaction, days: int):
        super().__init__(timeout=180)
        self.inter, self.days, self.branch = inter, days, "all"
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        self.add_item(BranchSelect(self.branch))

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if not is_curator(i.user):
            await i.response.send_message("❌ Нет прав.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.inter.edit_original_response(view=self)
        except discord.HTTPException:
            pass


class StaffToolsCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="activity", description="Активность строителей и тестеров (куратор)")
    @app_commands.guild_only()
    @app_commands.default_permissions(**PERMS_CURATOR)
    @app_commands.describe(days="Считать неактивным после N дней без сообщений и войса (по умолчанию 7)")
    async def activity_cmd(self, inter: discord.Interaction, days: app_commands.Range[int, 1, 90] = 7):
        if not is_curator(inter.user):
            return await inter.response.send_message("❌ Нет прав.", ephemeral=True)
        view = ActivityView(inter, days)
        await inter.response.send_message(embed=build_activity(inter.guild, "all", days), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(StaffToolsCog(bot))
