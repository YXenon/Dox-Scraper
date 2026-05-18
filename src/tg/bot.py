from telethon import TelegramClient
from .constants import API_ID, API_HASH
import logging
logging.basicConfig(format='[%(levelname) %(asctime)s] %(name)s: %(message)s', level=logging.WARNING)
client = TelegramClient("dox", api_id=API_ID, api_hash=API_HASH)