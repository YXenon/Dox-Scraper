import shutil
from pathlib import Path
import aiohttp
from camoufox import AsyncCamoufox
from .anilist import AnilistGenerator
from .converter import convert
from .progress import ProgressTracker
from .scraper import Scraper, Server
from handlers.process import app_ctx

async def scrape_job():
    # Remove junk if there's any
    try:
        shutil.rmtree(Path("./temp"))
    except FileNotFoundError:
        pass

    # Load progress from tracker or generate new items
    tracker = ProgressTracker(Path("record.json"))
    page, items = tracker.load()
    if items and len(items)>0:
        anime_list = items
    else:
        gen = AnilistGenerator(page, 1)
        anime_list = await gen.generate()

    # Initialise server, scraper, and browser
    scraper = Scraper()
    server = Server()
    server.launch()
    async with AsyncCamoufox(headless=True) as browser, aiohttp.ClientSession() as session:
        ctx = await browser.new_context() # type: ignore

        for i, anime in enumerate(anime_list):
            print(f"\n=> Scrape Job: ({i+1}/{len(anime_list)})")

            # Scrape the url
            metadata = await scraper.scrape(anime, ctx, session)

            # Convert to mkv if successfully found media and fetched
            if metadata:
                print("=> Converting to mkv")

                # Convert '.ts' to '.mkv'
                metadata = await convert(metadata)
            
                # Job sent to another process, to upload the file to Telegram
                app_ctx.data_q.put(metadata)
            
                print("=> Attempt data transfer to Telegram")
                is_ok = app_ctx.ok_q.get()
                if not (is_ok["job"] == "upload" and is_ok["status"] == "done"):
                    print("=> Issue with uploader")
                    break
            else:
                print("=> No metadata received!")

            # Deleting files in uploader for safety purpose

            # Save progress
            tracker.save(page, anime_list[i:])


    server.stop()
    tracker.save(page+1, None)