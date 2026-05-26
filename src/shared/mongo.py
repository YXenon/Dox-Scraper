import logging
from pymongo import AsyncMongoClient
from beanie import init_beanie
from .models import TelegramFile
from dotenv import load_dotenv
from os import getenv
load_dotenv()
logger = logging.getLogger(__name__)

async def init_mongo():
    client = AsyncMongoClient(getenv("MONGO_URI"))
    await client.aconnect()
    await init_beanie(database=client["dox_scraper"], document_models=[TelegramFile])
    logger.info("Connected to MongoDB")