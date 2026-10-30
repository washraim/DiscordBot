import asyncio
import traceback
import discord
from discord.ext import tasks
from bot_instance import bot
import config
import storage

COGS = ["cogs.economy", "cogs.moderation", "cogs.economy_admin", "cogs.admin_panel", "cogs.extras", "cogs.logs", "cogs.shop"]
_synced = False


@tasks.loop(seconds=30)
async def autosave():
    try:
        storage.flush()
    except Exception:
        traceback.print_exc()


async def sync_guild(guild: discord.Guild):
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ {guild.name}: синхронизировано {len(synced)} команд")
    except Exception as e:
        print(f"❌ Ошибка синхронизации {guild.name}: {e}")


@bot.event
async def setup_hook():
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            print(f"✅ Загружен {cog}")
        except Exception as e:
            print(f"❌ Ошибка загрузки {cog}: {e}")
    autosave.start()


@bot.event
async def on_ready():
    global _synced
    print(f"\n✅ Бот авторизован: {bot.user} (ID: {bot.user.id})")
    if _synced:
        return
    _synced = True

    for guild in bot.guilds:
        await sync_guild(guild)

    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        print("🧹 Глобальные команды удалены.")
    except Exception as e:
        print(f"❌ Ошибка удаления глобальных: {e}")
    print("🔄 Бот готов к работе!")


async def main():
    async with bot:
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    if not config.TOKEN:
        raise SystemExit("❌ Не задан DISCORD_TOKEN (переменная окружения или файл .env).")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен.")
    except Exception:
        traceback.print_exc()
    finally:
        storage.flush()
