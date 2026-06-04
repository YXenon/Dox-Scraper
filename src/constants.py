from pathlib import Path

from dotenv import load_dotenv
from os import getenv

load_dotenv()

API_ID = int(getenv("API_ID", ""))
API_HASH = getenv("API_HASH", "")
BOT_TOKEN = getenv("BOT_TOKEN", "")
STORE_CHANNEL_ID = int(getenv("STORE_CHANNEL_ID", "0"))
MONGO_URI = getenv("MONGO_URI")
DEV_ID = int(getenv("DEV_ID", "0"))

SCRAPE_PROVIDERS = [
    file.stem
    for file in (Path(__file__).parent / "scraper/providers").glob("*.py")
]