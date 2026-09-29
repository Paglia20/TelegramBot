import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = int(os.getenv("CHAT_ID", "0").strip() or 0)
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "").strip()
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "").strip()
EBAY_ENV = os.getenv("EBAY_ENV", "production").strip().lower()
EBAY_SANDBOX = EBAY_ENV == "sandbox"
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / ("monitor-sandbox.db" if EBAY_SANDBOX else "monitor.db"))))

EBAY_MARKETS = {
    "EBAY_IT": {"name": "eBay Italia", "country": "IT"},
    "EBAY_DE": {"name": "eBay Germania", "country": "DE"},
    "EBAY_CH": {"name": "eBay Svizzera", "country": "CH"},
    "EBAY_FR": {"name": "eBay Francia", "country": "FR"},
    "EBAY_BE": {"name": "eBay Belgio", "country": "BE"},
    "EBAY_NL": {"name": "eBay Olanda", "country": "NL"},
    "EBAY_AT": {"name": "eBay Austria", "country": "AT"},
    "EBAY_ES": {"name": "eBay Spagna", "country": "ES"},
    "EBAY_PL": {"name": "eBay Polonia", "country": "PL"},
    "EBAY_IE": {"name": "eBay Irlanda", "country": "IE"},
    "EBAY_GB": {"name": "eBay Regno Unito", "country": "GB"},
    "EBAY_US": {"name": "eBay USA", "country": "US"},
    "EBAY_CA": {"name": "eBay Canada", "country": "CA"},
    "EBAY_AU": {"name": "eBay Australia", "country": "AU"},
    "EBAY_HK": {"name": "eBay Hong Kong", "country": "HK"},
    "EBAY_SG": {"name": "eBay Singapore", "country": "SG"},
}

DEFAULT_INTERVAL_SECONDS = 120
MIN_INTERVAL_SECONDS = 30
MAX_INTERVAL_SECONDS = 3600
TIMER_PRESETS = [60, 90, 120, 180, 300, 600]
CYCLE_JITTER_SECONDS = 3

EBAY_DAILY_CALL_LIMIT = 5000
EBAY_BUDGET_SAFETY = 0.9
EBAY_ONLY_LOCAL_ITEMS = True
EBAY_QUERY_MODE = "combined"
EBAY_MAX_KEYWORDS_PER_QUERY = 5
EBAY_MAX_QUERY_CHARS = 100
EBAY_PAGE_SIZE = 50
EBAY_PAGE_SIZE_LARGE = 200
EBAY_MAX_PAGES = 3
EBAY_UPSCALE_IMAGES = True

HTTP_TIMEOUT_SECONDS = 10
MAX_PARALLEL_REQUESTS = 6
FAILURES_BEFORE_ALERT = 5
MAX_BACKOFF_SECONDS = 900

NEW_KEYWORD_LOOKBACK_MINUTES = 15
CROSS_MARKET_DEDUP = True
CROSS_MARKET_WINDOW_DAYS = 14
ON_LISTING_CHANGE = "price_drop"
SEEN_RETENTION_DAYS = 45

MAX_PHOTOS = 4
MAX_NOTIFICATIONS_PER_CYCLE = None
MAX_BACKLOG = 300

DASHBOARD_ENABLED = True
DASHBOARD_PUBLIC_URL = os.getenv("DASHBOARD_PUBLIC_URL", "").strip().rstrip("/")
DASHBOARD_HOST = "0.0.0.0" if DASHBOARD_PUBLIC_URL else "127.0.0.1"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or 8765)
DASHBOARD_EXTRA_HOSTS: list = []
DASHBOARD_LINK_MINUTES = 10
INVITE_HOURS = 24
DASHBOARD_SESSION_DAYS = 30

NOTIFY_OLD_LISTINGS = False

if EBAY_SANDBOX:
    EBAY_ONLY_LOCAL_ITEMS = False
    NOTIFY_OLD_LISTINGS = True
    MAX_NOTIFICATIONS_PER_CYCLE = 5
