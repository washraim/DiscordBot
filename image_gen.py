# image_gen.py
import asyncio
import io
import os
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import discord
from PIL import Image, ImageDraw, ImageFont
from config import FONT_BOLD, FONT_REG

W, H = 1920, 1080          # координаты в коде рассчитаны на этот размер
RED = (235, 30, 50)
WHITE = (255, 255, 255)
XP_PER_LEVEL = 6000

# ── Настройки скорости ─────────────────────────────────────────────
OUT_FORMAT = "JPEG"   # "JPEG" — быстро и в 5–8 раз легче; "PNG" — без потерь, но медленнее
OUT_SCALE = 1.0       # 0.75 → итоговая картинка 1440x810, ещё быстрее и легче
OUT_EXT = "jpg" if OUT_FORMAT == "JPEG" else "png"
AVATAR_CACHE_SIZE = 256
# ───────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_bg_cache = {"key": None, "img": None}
_avatar_cache: "OrderedDict[tuple, bytes]" = OrderedDict()


@lru_cache(maxsize=None)
def _font(path, size):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        raise FileNotFoundError(f"Не найден шрифт {Path(path).name} (положи его рядом с config.py)")


@lru_cache(maxsize=8)
def _circle_mask(size):
    """Круглая маска со сглаженными краями (считается один раз для каждого размера)."""
    big = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def _get_bg(bg_path):
    """Фон декодируется и масштабируется один раз; перечитывается, только если bg.png изменили."""
    st = os.stat(bg_path)
    key = (str(bg_path), st.st_mtime_ns)
    with _lock:
        if _bg_cache["key"] != key:
            img = Image.open(bg_path).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
            _bg_cache["key"], _bg_cache["img"] = key, img
        return _bg_cache["img"].copy()


def warm_up(bg_path):
    """Прогрев при старте: первый /profile не тратит время на загрузку фона и шрифтов."""
    try:
        if Path(bg_path).is_file():
            _get_bg(bg_path)
        for f, s in ((FONT_BOLD, 40), (FONT_BOLD, 28), (FONT_REG, 24), (FONT_REG, 20)):
            _font(f, s)
    except Exception as e:
        print(f"⚠️ Прогрев карточки профиля не удался: {e}")


async def _get_avatar_bytes(user):
    """Аватарки кэшируются по хэшу: при смене аватарки ключ меняется и скачивается новая."""
    asset = user.display_avatar
    key = (user.id, asset.key)
    if key in _avatar_cache:
        _avatar_cache.move_to_end(key)
        return _avatar_cache[key]

    # webp в разы легче png; для анимированных берём png (статичный первый кадр)
    fmt = "png" if asset.is_animated() else "webp"
    try:
        data = await asset.replace(size=512, format=fmt).read()
    except discord.HTTPException:
        data = await asset.replace(size=512, format="png").read()

    _avatar_cache[key] = data
    while len(_avatar_cache) > AVATAR_CACHE_SIZE:
        _avatar_cache.popitem(last=False)
    return data


def _render(bg_path, avatar_bytes, name, data, rank):
    bg = _get_bg(bg_path)
    draw = ImageDraw.Draw(bg)

    f_big = _font(FONT_BOLD, 40)
    f_med = _font(FONT_BOLD, 28)
    f_reg = _font(FONT_REG, 24)
    f_small = _font(FONT_REG, 20)

    # ═════════════ ЛЕВАЯ ПАНЕЛЬ ═════════════
    AV = 290
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((AV, AV), Image.Resampling.LANCZOS)
    bg.paste(avatar, (384 - AV // 2, 385 - AV // 2), _circle_mask(AV))

    display_name = name[:16] + "..." if len(name) > 16 else name
    draw.text((350, 608), display_name, font=f_med, fill=WHITE, anchor="mm")

    xp = data.get("xp", 0)
    level = xp // XP_PER_LEVEL + 1
    current_xp = xp % XP_PER_LEVEL
    draw.text((310, 775), f"{level} LVL", font=f_reg, fill=WHITE, anchor="lm")

    bar_x, bar_y, bar_w, bar_h = 140, 808, 507, 11
    fill_w = int(bar_w * current_xp / XP_PER_LEVEL)
    if fill_w > bar_h:
        draw.rounded_rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), radius=5, fill=RED)

    # Закрываем надпись "/ 100" из шаблона кусочком фона и пишем свою
    patch = bg.crop((590, 874, 656, 900))
    bg.paste(patch, (590, 832))
    draw.text((647, 845), f"{current_xp} / {XP_PER_LEVEL}", font=f_small, fill=WHITE, anchor="rm")

    # ═════════════ ПРАВАЯ ВЕРХНЯЯ ПАНЕЛЬ ═════════════
    VAL_Y = 548
    draw.text((935, VAL_Y), str(data.get("messages", 0)), font=f_big, fill=RED, anchor="lm")
    vt = data.get("voice_time", 0)
    draw.text((1313, VAL_Y), f"{vt // 3600}ч {(vt % 3600) // 60}м", font=f_big, fill=RED, anchor="lm")
    draw.text((1680, VAL_Y), rank, font=f_big, fill=RED, anchor="lm")

    # ═════════════ ПРАВАЯ НИЖНЯЯ ПАНЕЛЬ ═════════════
    ECO_Y = 880
    draw.text((950, ECO_Y), f"{data.get('salary', 0):,}".replace(",", " "), font=f_big, fill=RED, anchor="lm")
    draw.text((1465, ECO_Y), f"{data.get('points', 0):,}".replace(",", " "), font=f_big, fill=WHITE, anchor="lm")

    # ═════════════ СОХРАНЕНИЕ ═════════════
    if OUT_SCALE != 1.0:
        bg = bg.resize((int(W * OUT_SCALE), int(H * OUT_SCALE)), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if OUT_FORMAT == "JPEG":
        # subsampling=0 сохраняет чёткость красного текста на тёмном фоне
        bg.save(out, format="JPEG", quality=90, subsampling=0)
    else:
        bg.save(out, format="PNG", compress_level=1)
    out.seek(0)
    return out


async def generate_profile_card(bg_path, user, data, top_users, guild):
    if not Path(bg_path).is_file():
        return None

    avatar_bytes = await _get_avatar_bytes(user)

    rank = "—"
    for idx, (uid, _) in enumerate(top_users):
        if int(uid) == user.id:
            rank = f"#{idx + 1}"
            break

    # Pillow синхронный, поэтому рисуем в отдельном потоке, чтобы не вешать бота
    return await asyncio.to_thread(_render, bg_path, avatar_bytes, user.display_name, data, rank)
