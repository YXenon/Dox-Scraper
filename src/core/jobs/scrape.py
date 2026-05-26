import asyncio
import logging
import traceback
from scraper.launch import scrape_job
from shared.logger import config_logger

def launch_scrape_job(*_):
    config_logger()
    logger = logging.getLogger(__name__)
    logger.info("Starting scraper")
    try:
        asyncio.run(scrape_job())
    except KeyboardInterrupt:
        pass
    except Exception:
        print(traceback.format_exc())