from pathlib import Path

from dotenv import load_dotenv
from os import getenv

load_dotenv()

API_ID = int(getenv("API_ID", ""))
API_HASH = getenv("API_HASH", "")
BOT_TOKEN = getenv("BOT_TOKEN", "")
DEVS = []
STORE_CHANNEL_ID = -1003181201171

SCRAPE_PROVIDERS = [
    file.stem
    for file in (Path(__file__).parent / "scraper/providers").glob("*.py")
]
