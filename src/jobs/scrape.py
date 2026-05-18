import asyncio
from scraper.launch import scrape_job

def launch_scrape_job(*_):
    print("=> Starting scraper")
    asyncio.run(scrape_job())