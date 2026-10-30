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
