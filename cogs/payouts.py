import discord
from discord.ext import commands
from config import PAYOUT_MAX_AMOUNT, PAYOUT_REPLY_IN_CHANNEL
from storage import (
    get_settings, get_user, add_salary, log_salary, payout_done, payout_mark, payout_get,
    payout_set_log, payout_cancel, flush,
)
from helpers import is_curator, is_owner, is_staff, send_log, send_log_msg
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


def walk_components(components):
    texts = []
    for c in components or []:
        content = getattr(c, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for attr in ("children", "components"):
            sub = getattr(c, attr, None)
            if sub:
                texts.extend(walk_components(sub))
        accessory = getattr(c, "accessory", None)
        if accessory is not None:
            texts.extend(walk_components([accessory]))
    return texts


def collect_text(message):
    parts = [message.content or ""]
    parts.extend(walk_components(getattr(message, "components", None)))
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


class CancelModal(discord.ui.Modal, title="Отменить выплату"):
    reason = discord.ui.TextInput(label="Причина (необязательно)", style=discord.TextStyle.paragraph,
                                  required=False, max_length=300)

    def __init__(self, mid):
        super().__init__()
        self.mid = mid

    async def on_submit(self, i: discord.Interaction):
        if not is_curator(i.user):
            return await i.response.send_message("❌ Нет прав.", ephemeral=True)
        rec, status = await cancel_payout(i.client, self.mid, i.user.id, self.reason.value.strip() or None)
        if status == "already":
            return await i.response.send_message("ℹ️ Эта выплата уже отменена.", ephemeral=True)
        if rec is None:
            return await i.response.send_message("❌ Запись о выплате не найдена.", ephemeral=True)
        c = rec["cancelled"]
        text = f"✅ Выплата отменена: у <@{rec['uid']}> списано **{num(c['returned'])}** ЗП."
        if c["shortfall"]:
            text += f"\n⚠️ На счёте не хватило {num(c['shortfall'])} ЗП (часть уже потрачена), списано только доступное."
        await i.response.send_message(text, ephemeral=True)


class PayoutCancelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"payout_cancel:(?P<id>\d+)"):
    def __init__(self, mid: int):
        super().__init__(discord.ui.Button(label="Отменить выплату", emoji="↩️", style=discord.ButtonStyle.danger,
                                           custom_id=f"payout_cancel:{mid}"))
        self.mid = mid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["id"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_curator(interaction.user):
            await interaction.response.send_message("❌ Отменять выплаты могут только кураторы.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction):
        rec = payout_get(self.mid)
        if rec is None:
            return await interaction.response.send_message("❌ Запись о выплате не найдена.", ephemeral=True)
        if rec.get("cancelled"):
            return await interaction.response.send_message("ℹ️ Эта выплата уже отменена.", ephemeral=True)
        await interaction.response.send_modal(CancelModal(self.mid))


async def cancel_payout(client, mid, by, reason=None):
    rec, status = payout_cancel(mid, by, reason)
    if status != "ok":
        return rec, status
    c = rec["cancelled"]
    who = "автоматически" if by == "auto" else f"<@{by}>"

    if rec.get("log_channel_id") and rec.get("log_message_id"):
        channel = client.get_channel(rec["log_channel_id"])
        try:
            msg = await channel.fetch_message(rec["log_message_id"]) if channel else None
            if msg is not None and msg.embeds:
                emb = discord.Embed.from_dict(msg.embeds[0].to_dict())
                emb.title = "↩️ Выплата отменена"
                emb.color = 0x95A5A6
                emb.add_field(name="Отменил", value=who, inline=True)
                emb.add_field(name="Списано", value=f"**{num(c['returned'])}** ЗП", inline=True)
                if reason:
                    emb.add_field(name="Причина", value=reason[:500], inline=False)
                await msg.edit(embed=emb, view=None)
        except discord.HTTPException:
            pass

    if rec.get("src_channel"):
        try:
            channel = client.get_channel(rec["src_channel"])
            if channel is not None:
                await channel.get_partial_message(mid).add_reaction("↩️")
        except discord.HTTPException:
            pass

    e = discord.Embed(title="↩️ Выплата отменена", color=RED, timestamp=discord.utils.utcnow())
    e.add_field(name="Получатель", value=f"<@{rec['uid']}>", inline=True)
    e.add_field(name="Отменил", value=who, inline=True)
    e.add_field(name="Списано", value=f"**{num(c['returned'])}** ЗП (было начислено {num(rec['net'])})", inline=True)
    if c["shortfall"]:
        e.add_field(name="Не хватило на счёте", value=f"{num(c['shortfall'])} ЗП", inline=True)
    if reason:
        e.add_field(name="Причина", value=reason[:500], inline=False)
    await send_log(client, "pay_log", e)
    return rec, "ok"


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
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        rec = payout_get(payload.message_id)
        if rec is not None and not rec.get("cancelled"):
            await cancel_payout(self.bot, payload.message_id, "auto", "исходное сообщение с выплатой удалено")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        for mid in payload.message_ids:
            rec = payout_get(mid)
            if rec is not None and not rec.get("cancelled"):
                await cancel_payout(self.bot, mid, "auto", "исходное сообщение с выплатой удалено")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if collect_text(before) != collect_text(after):
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
        text = collect_text(message)
        parsed = parse_payout(text)
        if parsed is None:
            if not text.strip() and (message.embeds or getattr(message, "components", None)):
                await self.respond(message, "⚠️", discord.Embed(
                    title="⚠️ Не вижу текста в сообщении",
                    description="Бот не смог прочитать содержимое. Проверьте, что в настройках бота включён "
                                "Message Content Intent и что у бота есть право читать этот канал.",
                    color=RED))
            return
        if payout_done(message.id):
            return

        error, member = parsed.get("error"), None
        if not error:
            member, error = self.resolve(message.guild, parsed, branch)
        if not error and not automated and member.id == message.author.id and not is_owner(message.author):
            error = "Нельзя начислять выплату самому себе."
        if not error and parsed["amount"] > PAYOUT_MAX_AMOUNT:
            error = f"Сумма больше допустимого лимита ({num(PAYOUT_MAX_AMOUNT)}). Начислите вручную через `/economy`."
        if error:
            e = discord.Embed(title="⚠️ Выплата не начислена", description=f"{error}\n\n{FORMAT_HINT}", color=RED)
            snippet = text.replace("```", "'" * 3)[:500]
            e.add_field(name="Что прочитал бот", value=f"```{snippet}```", inline=False)
            return await self.respond(message, "⚠️", e)

        amount = parsed["amount"]
        pen = min(get_user(member.id)["warnings"] * 10, 50)
        net = amount * (100 - pen) // 100
        if net <= 0:
            return await self.respond(message, "⚠️", discord.Embed(
                title="⚠️ Выплата не начислена", description="После штрафа сумма равна нулю.", color=RED))

        add_salary(member.id, net)
        log_salary(member.id, amount, net, message.author.id, "pay", "message", message.jump_url)
        payout_mark(message.id, member.id, amount, net, message.channel.id)
        flush()

        e = discord.Embed(title="✅ Выплата начислена", color=GREEN, timestamp=discord.utils.utcnow())
        e.add_field(name="Получатель", value=member.mention, inline=True)
        e.add_field(name="Начислено", value=f"**{num(net)}** ЗП", inline=True)
        e.add_field(name="Баланс ЗП", value=num(get_user(member.id)["salary"]), inline=True)
        if pen:
            e.add_field(name="Штраф за выговоры", value=f"−{pen}% (в сообщении: {num(amount)})", inline=False)
        log = e.copy()
        log.url = message.jump_url
        log.add_field(name="Источник", value=message.author.mention, inline=True)
        if branch:
            log.add_field(name="Ветка", value=BRANCH_NAMES[branch], inline=True)
        log.add_field(name="Сообщение", value=f"[перейти]({message.jump_url})", inline=True)
        view = discord.ui.View(timeout=None)
        view.add_item(PayoutCancelButton(message.id))
        log_channel_id = get_settings().get("pay_log")
        sent = await send_log_msg(self.bot, "pay_log", log, view=view)
        if sent is not None:
            payout_set_log(message.id, sent.channel.id, sent.id)

        if sent is not None and (not PAYOUT_REPLY_IN_CHANNEL or log_channel_id == message.channel.id):
            try:
                await message.add_reaction("✅")
            except discord.HTTPException:
                pass
        elif sent is None:
            reply = await self.respond(message, "✅", e, view)
            if reply is not None:
                payout_set_log(message.id, reply.channel.id, reply.id)
        else:
            await self.respond(message, "✅", e)

    async def respond(self, message, emoji, embed, view=None):
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass
        try:
            return await message.reply(embed=embed, view=view, mention_author=False)
        except discord.HTTPException:
            return None


async def setup(bot):
    bot.add_dynamic_items(PayoutCancelButton)
    await bot.add_cog(PayoutsCog(bot))
