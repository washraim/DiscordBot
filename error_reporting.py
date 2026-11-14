import functools
import sys
import time
import traceback
import discord
from discord import app_commands
from helpers import send_log

RED = 0x9B111E
SUPPRESS_SECONDS = 60
USER_MESSAGE = "⚠️ Произошла ошибка. Куратор уведомлён."

_recent = {}


def format_traceback(error):
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


async def report_error(bot, title, error, context=None, force=False):
    tb = format_traceback(error)
    print(tb, file=sys.stderr)
    key = (type(error).__name__, str(error)[:120])
    now = time.monotonic()
    if not force and now - _recent.get(key, 0) < SUPPRESS_SECONDS:
        return False
    _recent[key] = now

    e = discord.Embed(title=f"🚨 {title}", color=RED, timestamp=discord.utils.utcnow())
    e.add_field(name="Ошибка", value=f"`{type(error).__name__}`: {str(error)[:300] or '—'}", inline=False)
    for name, value in (context or {}).items():
        e.add_field(name=name, value=(str(value)[:1000] or "—"), inline=True)
    tail = tb[-3000:].replace("```", "'" * 3)
    e.description = f"```py\n{tail}\n```"
    return await send_log(bot, "error_log", e)


def guard(name):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            try:
                return await fn(self, *args, **kwargs)
            except Exception as exc:
                await report_error(self.bot, f"Фоновая задача: {name}", exc)
        return wrapper
    return decorator


def interaction_context(interaction, source):
    ctx = {"Источник": source}
    if interaction.user:
        ctx["Участник"] = f"{interaction.user.mention} (`{interaction.user.id}`)"
    if interaction.channel_id:
        ctx["Канал"] = f"<#{interaction.channel_id}>"
    return ctx


async def tell_user(interaction):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(USER_MESSAGE, ephemeral=True)
        else:
            await interaction.response.send_message(USER_MESSAGE, ephemeral=True)
    except discord.HTTPException:
        pass


def install(bot):
    async def tree_error(interaction, error):
        original = getattr(error, "original", error)
        name = f"/{interaction.command.qualified_name}" if interaction.command else "неизвестная команда"
        await report_error(bot, "Ошибка команды", original, interaction_context(interaction, name))
        await tell_user(interaction)

    async def event_error(event_method, *args, **kwargs):
        exc = sys.exc_info()[1]
        if exc is None:
            return
        await report_error(bot, "Ошибка в обработчике события", exc, {"Событие": event_method})

    async def view_error(self, interaction, error, item):
        await report_error(bot, "Ошибка в кнопке или меню", error,
                           interaction_context(interaction, type(self).__name__))
        await tell_user(interaction)

    async def modal_error(self, interaction, error):
        await report_error(bot, "Ошибка в форме", error, interaction_context(interaction, type(self).__name__))
        await tell_user(interaction)

    async def item_error(self, interaction, error):
        await report_error(bot, "Ошибка в кнопке", error, interaction_context(interaction, type(self).__name__))
        await tell_user(interaction)

    bot.tree.on_error = tree_error
    bot.on_error = event_error
    discord.ui.View.on_error = view_error
    discord.ui.Modal.on_error = modal_error
    discord.ui.DynamicItem.on_error = item_error
    if hasattr(discord.ui, "LayoutView"):
        discord.ui.LayoutView.on_error = view_error
