import discord
from discord.ext import commands
from config import PAYOUT_MAX_AMOUNT
from storage import get_settings, get_user, add_salary, log_salary, payout_done, payout_mark, flush
from helpers import is_curator, is_owner, is_staff, send_log
from payout_parser import parse_payout, find_candidates

RED = 0x9B111E
GREEN = 0x2ECC71

CHANNEL_SCOPES = {
    "payout_builder_channel": "builder",
    "payout_tester_channel": "tester",
}
BRANCH_NAMES = {"builder": "строителей", "tester": "тестеров"}

FORMAT_HINT = ("Подойдёт любой из форматов: строки `Выплата: 5000` и `Ник: Steve` (в заголовке, описании или полях "
               "эмбеда) либо простой текст `Выплата 5000 Steve`. Исправьте сообщение, бот проверит его заново.")


def num(v):
    return f"{v:,}".replace(",", " ")


def collect_text(message):
    parts = [message.content or ""]
    for e in message.embeds:
        if e.author and e.author.name:
            parts.append(e.author.name)
        if e.title:
            parts.append(e.title)
        if e.description:
            parts.append(e.description)
        for f in e.fields:
            parts.append(f"{f.name}: {f.value}")
        if e.footer and e.footer.text:
            parts.append(e.footer.text)
    return "\n".join(parts)


def channel_scope(channel_id):
    s = get_settings()
    for key, branch in CHANNEL_SCOPES.items():
        if s.get(key) == channel_id:
            return True, branch
    return False, None


class PayoutsCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.handle(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        same = (before.content == after.content
                and [e.to_dict() for e in before.embeds] == [e.to_dict() for e in after.embeds])
        if not same:
            await self.handle(after)

    def resolve(self, guild, parsed, branch):
        pool, where = guild.members, "на сервере"
        if branch:
            role = guild.get_role(get_settings().get(f"{branch}_role") or 0)
            if role is None:
                return None, f"Роль {BRANCH_NAMES[branch]} не настроена в `/admin`."
            pool, where = role.members, f"среди {BRANCH_NAMES[branch]}"

        if "user_id" in parsed:
            member = guild.get_member(parsed["user_id"])
            if member is None:
                return None, "Участник из упоминания не найден на сервере."
            if branch and member not in pool:
                return None, f"{member.mention} не состоит в ветке {BRANCH_NAMES[branch]}."
            candidates = [member]
        else:
            candidates = find_candidates(parsed["nick"], pool)
            if not candidates:
                return None, f"Не нашёл {where} участника с ником **{parsed['nick']}**. Укажите ник так, как он написан на сервере."

        staff = [m for m in candidates if not m.bot and is_staff(m)]
        if len(staff) == 1:
            return staff[0], None
        if not staff:
            return None, f"{candidates[0].mention} не относится к персоналу, выплата не начислена."
        return None, "Найдено несколько сотрудников с таким ником: " + ", ".join(m.mention for m in staff[:5]) + ". Уточните ник."

    async def handle(self, message: discord.Message):
        if not message.guild or message.author.id == self.bot.user.id:
            return
        watched, branch = channel_scope(message.channel.id)
        if not watched:
            return
        automated = message.author.bot or message.webhook_id is not None
        if not automated and not is_curator(message.author):
            return
        parsed = parse_payout(collect_text(message))
        if parsed is None or payout_done(message.id):
            return

        error, member = parsed.get("error"), None
        if not error:
            member, error = self.resolve(message.guild, parsed, branch)
        if not error and not automated and member.id == message.author.id and not is_owner(message.author):
            error = "Нельзя начислять выплату самому себе."
        if not error and parsed["amount"] > PAYOUT_MAX_AMOUNT:
            error = f"Сумма больше допустимого лимита ({num(PAYOUT_MAX_AMOUNT)}). Начислите вручную через `/economy`."
        if error:
            return await self.respond(message, "⚠️", discord.Embed(
                title="⚠️ Выплата не начислена", description=f"{error}\n\n{FORMAT_HINT}", color=RED))

        amount = parsed["amount"]
        pen = min(get_user(member.id)["warnings"] * 10, 50)
        net = amount * (100 - pen) // 100
        if net <= 0:
            return await self.respond(message, "⚠️", discord.Embed(
                title="⚠️ Выплата не начислена", description="После штрафа сумма равна нулю.", color=RED))

        add_salary(member.id, net)
        log_salary(member.id, amount, net, message.author.id, "pay")
        payout_mark(message.id, member.id, amount, net)
        flush()

        e = discord.Embed(title="✅ Выплата начислена", color=GREEN, timestamp=discord.utils.utcnow())
        e.add_field(name="Получатель", value=member.mention, inline=True)
        e.add_field(name="Начислено", value=f"**{num(net)}** ЗП", inline=True)
        e.add_field(name="Баланс ЗП", value=num(get_user(member.id)["salary"]), inline=True)
        if pen:
            e.add_field(name="Штраф за выговоры", value=f"−{pen}% (в сообщении: {num(amount)})", inline=False)
        await self.respond(message, "✅", e)

        if get_settings().get("pay_log") != message.channel.id:
            log = discord.Embed(title="🧾 Выплата по сообщению", color=GREEN, timestamp=discord.utils.utcnow(),
                                url=message.jump_url)
            log.add_field(name="Получатель", value=f"{member.mention}\n`{member.id}`", inline=True)
            log.add_field(name="Сумма", value=f"**{num(net)}** ЗП", inline=True)
            log.add_field(name="Источник", value=message.author.mention, inline=True)
            if branch:
                log.add_field(name="Ветка", value=BRANCH_NAMES[branch], inline=True)
            if pen:
                log.add_field(name="Штраф", value=f"−{pen}% (было {num(amount)})", inline=True)
            log.add_field(name="Сообщение", value=f"[перейти]({message.jump_url})", inline=False)
            await send_log(self.bot, "pay_log", log)

    async def respond(self, message, emoji, embed):
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass
        try:
            await message.reply(embed=embed, mention_author=False)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(PayoutsCog(bot))
