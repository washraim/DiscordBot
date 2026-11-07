import re

KEYWORD = re.compile(r"выплат", re.I)
NUM = r"(\d[\d \u00a0.,]*\d|\d)"
AMOUNT_AFTER_KEYWORD = re.compile(r"выплат\w*[\s*_~]*[:=\-–—]?[\s*_~]*" + NUM, re.I)
AMOUNT_LABELED = re.compile(r"\b(?:сумма|размер|amount)[\s*_~]*[:=\-–—][\s*_~]*" + NUM, re.I)
NICK_LABEL = re.compile(
    r"^[^\w@<]*(?:ник(?:нейм)?|nick(?:name)?|игрок|кому|получатель|сотрудник|стафф|staff)"
    r"[^\w\n]*[:=\-–—]\s*(.+?)\s*$",
    re.I | re.M,
)
MENTION = re.compile(r"<@!?(\d+)>")
THOUSANDS = re.compile(r"^\d{1,3}(?:[ .,]\d{3})+$")
KEYWORD_WORD = re.compile(r"выплат\w*", re.I)
CURRENCY = r"(?:зп|зарплат\w*|руб\w*|баллов|балла|балл|монет\w*)"
STANDALONE = re.compile(r"(?<!\w)" + NUM + r"(?=\s*" + CURRENCY + r"(?!\w)|(?!\w))", re.I)
CURRENCY_WORD = re.compile(r"(?<!\w)" + CURRENCY + r"(?!\w)", re.I)
STOP_WORDS = {"для", "на", "игроку", "стаффу", "сотруднику", "участнику", "за", "по", "кому", "ник", "nick"}
SEGMENT_SPLIT = re.compile(r"[|/\\•·()\[\]{}«»,;]|\s[–—-]\s")


def parse_amount(blob):
    blob = blob.replace("\u00a0", " ").strip()
    if re.fullmatch(r"\d+", blob):
        return int(blob)
    if THOUSANDS.match(blob):
        return int(re.sub(r"\D", "", blob))
    return None


def clean_nick(value):
    value = re.sub(r"[*~`]", "", value).strip()
    value = value.strip("\"'«»“”")
    if value.startswith("@"):
        value = value[1:]
    return value.strip()


def leftover_nick(line):
    s = KEYWORD_WORD.sub(" ", line)
    s = STANDALONE.sub(" ", s, count=1)
    s = CURRENCY_WORD.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" \t*_~`:=-–—→>,;.|")
    words = s.split(" ") if s else []
    while words and words[0].casefold() in STOP_WORDS:
        words.pop(0)
    s = " ".join(words).strip(" \t*_~`:=-–—→>,;.|")
    if s.startswith("@"):
        s = s[1:].strip()
    if not s or len(s) > 40 or len(s.split(" ")) > 5:
        return None
    return s


def parse_payout(text):
    text = text.replace("\u00a0", " ")
    if not KEYWORD.search(text):
        return None

    values, unclear = set(), False
    for pattern in (AMOUNT_AFTER_KEYWORD, AMOUNT_LABELED):
        for m in pattern.finditer(text):
            v = parse_amount(m.group(1))
            if v is None:
                unclear = True
            else:
                values.add(v)
    if unclear:
        return {"error": "Сумма записана неоднозначно (дробное число или лишние цифры). "
                         "Укажите целое число, например `Выплата: 5000`."}
    if len(values) > 1:
        return {"error": "В сообщении несколько разных сумм: " + ", ".join(str(v) for v in sorted(values)) + "."}

    lines = [ln for ln in text.split("\n") if ln.strip()]
    key_idx = next((i for i, ln in enumerate(lines) if KEYWORD.search(ln)), 0)

    if values:
        amount = values.pop()
    else:
        scope = lines[key_idx] if STANDALONE.search(lines[key_idx]) else "\n".join(lines)
        blobs = [m.group(1) for m in STANDALONE.finditer(scope)]
        if not blobs:
            return {"error": "Не нашёл сумму. Напишите, например, `Выплата: 5000`."}
        if len(blobs) > 1:
            return {"error": "Не смог однозначно определить сумму: в тексте несколько чисел. "
                             "Напишите, например, `Выплата: 5000`."}
        amount = parse_amount(blobs[0])
        if amount is None:
            return {"error": "Сумма записана неоднозначно. Укажите целое число, например `Выплата: 5000`."}

    m = NICK_LABEL.search(text)
    if m:
        raw = clean_nick(m.group(1))
        mention = MENTION.search(raw)
        if mention:
            return {"amount": amount, "user_id": int(mention.group(1))}
        if raw:
            return {"amount": amount, "nick": raw}

    ids = set(MENTION.findall(text))
    if len(ids) == 1:
        return {"amount": amount, "user_id": int(ids.pop())}
    if len(ids) > 1:
        return {"error": "В сообщении несколько упоминаний. Укажите получателя строкой `Ник: ...`."}

    for ln in lines[key_idx:key_idx + 2]:
        nick = leftover_nick(ln)
        if nick:
            return {"amount": amount, "nick": nick}
    return {"error": "Не нашёл ник получателя. Напишите `Ник: Steve` или `Выплата 5000 Steve`."}


def norm(value):
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def names_of(member):
    return [norm(member.display_name), norm(getattr(member, "global_name", None)), norm(member.name)]


def find_candidates(nick, members):
    n = norm(nick)
    if not n:
        return []
    exact = [m for m in members if n in names_of(m)]
    if exact:
        return exact
    return [m for m in members
            if any(norm(seg) == n for seg in SEGMENT_SPLIT.split(m.display_name or ""))]
