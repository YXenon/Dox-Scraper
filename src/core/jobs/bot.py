import asyncio
import logging
import traceback
from shared.logger import config_logger
from telegram.launch import telegram_jobs

def launch_bot_job(*_):
    config_logger()
    logger = logging.getLogger(__name__)
    logger.info("Starting bot and uploader")
    
    try:
        asyncio.run(telegram_jobs())
    except KeyboardInterrupt:
        pass
    except Exception:
        print(traceback.format_exc())