import os
from pathlib import Path

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

BG_PATH   = BASE_DIR / "bg.png"
FONT_BOLD = BASE_DIR / "Montserrat-Bold.ttf"
FONT_REG  = BASE_DIR / "Montserrat-Regular.ttf"

PERMS_OWNER       = {"administrator": True}
PERMS_CURATOR     = {"manage_messages": True}
PERMS_RESPONSIBLE = {"moderate_members": True}
PERMS_STAFF = {"manage_events": True}

ORDERS_F     = DATA / "orders.json"
SALARY_LOG_F = DATA / "salary_log.json"
WARNS_F      = DATA / "warns.json"
PAYOUTS_F    = DATA / "payouts.json"

WARN_EXPIRE_DAYS = 14

DAILY_VOICE_POINTS_LIMIT = 500
DAILY_TEXT_POINTS_LIMIT = 600
DAY_UTC_OFFSET_HOURS = 5
PAYOUT_MAX_AMOUNT = 1000000
PAYOUT_REPLY_IN_CHANNEL = False
SALARY_REPORT_DAY = 27
SALARY_REPORT_HOUR = 12
