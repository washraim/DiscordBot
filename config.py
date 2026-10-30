import os
from pathlib import Path

# Токен берём из переменной окружения DISCORD_TOKEN (или из файла .env рядом с ботом).
# Файл .env:  DISCORD_TOKEN=твой_новый_токен
# Для .env нужен пакет:  pip install python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.environ.get("DISCORD_TOKEN", "")
OWNER_ID = 673564170167255041

BASE_DIR = Path(__file__).resolve().parent

DATA = BASE_DIR / "data"
DATA.mkdir(exist_ok=True)
USERS_F    = DATA / "users.json"
SHOP_F     = DATA / "shop.json"
STAFF_F    = DATA / "staff.json"
SETTINGS_F = DATA / "settings.json"

# Ресурсы для карточки профиля (лежат рядом с config.py)
BG_PATH   = BASE_DIR / "bg.png"
FONT_BOLD = BASE_DIR / "Montserrat-Bold.ttf"
FONT_REG  = BASE_DIR / "Montserrat-Regular.ttf"

# ── Видимость слэш-команд ──────────────────────────────────────────
# Discord показывает команду в списке только тем, у кого есть указанное право
# (администраторы видят всё). Права вешаются на Discord-роли кураторов и
# ответственных, поэтому команды появляются сами, когда участник получает роль.
# Проверки внутри бота остаются: это только «витрина».
PERMS_OWNER       = {"administrator": True}     # /admin
PERMS_CURATOR     = {"manage_messages": True}   # /economy, /adm_shop, /warn_clear
PERMS_RESPONSIBLE = {"moderate_members": True}  # /warn
