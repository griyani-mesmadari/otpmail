import os
from pathlib import Path
from dotenv import load_dotenv

# Base Directory
BASE_DIR = Path(__file__).resolve().parent

# Load .env
load_dotenv(BASE_DIR / ".env")

# Telegram Bot Settings
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Admin IDs (comma-separated strings or integers, e.g. "12345678,87654321")
raw_admins = os.getenv("ADMIN_IDS", "12345678").strip()
ADMIN_IDS = [int(x.strip()) for x in raw_admins.split(",") if x.strip().isdigit()]

# Pricing & Balance Settings
OTP_PRICE = int(os.getenv("OTP_PRICE", "200"))  # Biaya per OTP Rp 200
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "5000"))  # Minimal top-up Rp 5.000

# SmailPro Automation Settings
SMAILPRO_API_KEY = os.getenv("SMAILPRO_API_KEY", "").strip()
SMAILPRO_HEADLESS = os.getenv("SMAILPRO_HEADLESS", "true").lower() in ("true", "1", "yes")

# Environment-based Session (for cloud deployments like Render/Heroku)
SMAILPRO_SESSION_JSON = os.getenv("SMAILPRO_SESSION_JSON", "").strip()
SMAILPRO_SESSION_B64 = os.getenv("SMAILPRO_SESSION_B64", "").strip()

# Directories
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bot_otp.db"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
SESSION_STATE_FILE = DATA_DIR / "smailpro_session.json"

# Port for Render Healthcheck Web Service (Render automatically sets $PORT)
PORT = int(os.getenv("PORT", "10000"))

# Static QRIS String (PT SERVER HANDAL, SUKOLIL - SURABAYA)
STATIC_QRIS = os.getenv(
    "STATIC_QRIS",
    "00020101021126610014COM.GO-JEK.WWW01189360091434782190150210G4782190150303UMI51440014ID.CO.QRIS.WWW0215ID10253756120210303UMI5204581553033605802ID5925PT SERVER HANDAL, SUKOLIL6008SURABAYA61056011762070703A0163042EB1"
).strip()

# Payment Information for Top Up
PAYMENT_QRIS_URL = os.getenv("PAYMENT_QRIS_URL", "").strip()
PAYMENT_DANA = os.getenv("PAYMENT_DANA", "081234567890 (A/N Admin)")
PAYMENT_GOPAY = os.getenv("PAYMENT_GOPAY", "081234567890 (A/N Admin)")
PAYMENT_OVO = os.getenv("PAYMENT_OVO", "081234567890 (A/N Admin)")
PAYMENT_BANK = os.getenv("PAYMENT_BANK", "BCA: 1234567890 a.n Admin")

# Order expiry in minutes
ORDER_EXPIRE_MINUTES = int(os.getenv("ORDER_EXPIRE_MINUTES", "20"))
