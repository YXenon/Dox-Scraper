import shutil
from pathlib import Path
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
    page, item, items = tracker.load()
    if item and items:
        anime_list = items[item:]
    else:
        gen = AnilistGenerator(page, 1)
        anime_list = await gen.generate()

    # Initialise server, scraper, and browser
    scraper = Scraper()
    server = Server()
    server.launch()
    async with AsyncCamoufox(headless=False) as browser:
        ctx = await browser.new_context() # type: ignore

        for i, anime in enumerate(anime_list):
            print(f"\n=> Scrape Job: ({i+1}/{len(anime_list)})")

            # Scrape the url
            metadata = await scraper.scrape(anime, ctx)

            # Convert to mp4 if successfully found media and fetched
            if metadata:
                print("=> Converting to mp4")

                # Convert '.ts' to '.mp4'
                metadata = convert(metadata)
            
            # Job sent to another process, to upload the file to Telegram
            app_ctx.data_q.put(metadata)
            
            ok = False
            while True:
                is_ok = app_ctx.ok_q.get()
                if is_ok["job"] == "upload":
                    if is_ok["status"] == "done":
                        ok = True
                    break
            if not ok:
                print("=> ISSUE WITH SCRAPER, STATUS NOT OK")
                break

            # Save progress
            tracker.save(page, i, anime_list)


    server.stop()
    tracker.save(page+1, None, None)