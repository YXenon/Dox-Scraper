import logging
from pymongo import AsyncMongoClient
from beanie import init_beanie

from constants import MONGO_URI
from .models import ScrapedAnime, TelegramFile

logger = logging.getLogger(__name__)

async def init_mongo():
    client = AsyncMongoClient(MONGO_URI)
    await client.aconnect()
    await init_beanie(database=client["dox_scraper"], document_models=[TelegramFile, ScrapedAnime])
    logger.info("Connected to MongoDB")