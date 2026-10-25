import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.moderation = True   # события банов (входит в default, указано явно)

# max_messages: сколько сообщений хранится в кэше. Нужно для логов удаления и изменения
bot = commands.Bot(command_prefix="!", intents=intents, max_messages=10000)
