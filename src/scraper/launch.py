import asyncio
import copy
import logging
import shutil
import traceback
from pathlib import Path
from typing import Any

import aiohttp
from camoufox import AsyncCamoufox
from playwright.async_api import BrowserContext

from shared.models import Metadata, RequestedMode, ScrapingRequests, TelegramFile
from shared.mongo import init_mongo

from .anilist import Anilist
from .converter import convert
from .progress import ProgressTracker
from .proxy import ProxyServer
from .providers.anikoto import URLBuilder, Scraper
from core.handlers.process import app_ctx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMP_DIR     = Path("./temp")
RECORD_FILE  = Path("record.json")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_temp() -> None:
    """Remove the temp directory and all its contents if it exists."""
    try:
        shutil.rmtree(TEMP_DIR)
    except FileNotFoundError:
        pass


async def _load_or_generate_anime_list(
    tracker: ProgressTracker,
) -> tuple[int, list]:
    """
    Return saved progress if available, otherwise generate a fresh anime list.

    Each item in the returned list has the shape::

        {"info": <anilist dict>, "entries": <list of episode entry dicts>}
    """
    page, items = tracker.load()
    if items:
        return page, items

    raw_anime_list = await Anilist().generate(page, 1)
    grouped_entries = URLBuilder(anime_list=raw_anime_list).build()

    anime_list: list[dict[str, list[dict[str, str]] | dict[str, Any]]] = [
        {"info": data, "entries": entry}
        for data, entry in zip(raw_anime_list, grouped_entries)
    ]
    return page, anime_list


async def _upload_to_telegram(metadata: Metadata) -> tuple[bool, Any, Any]:
    """
    Serialize metadata and send it to the Telegram uploader process via a queue.

    Blocks until the uploader acknowledges completion on the ok queue.

    Returns
    -------
    success    : True when the uploader reports job=upload, status=done.
    channel_id : Telegram channel ID the file was sent to.
    msg_id     : Message ID of the uploaded file.
    """
    loop = asyncio.get_event_loop()
    app_ctx.scraper_conn.send(metadata.model_dump_json())
    response: dict = await loop.run_in_executor(None, app_ctx.scraper_conn.recv)

    success = response.get("job") == "upload" and response.get("status") == "done"
    return success, response.get("channel_id"), response.get("msg_id")


def _compute_remaining_track(anime_list: list, anime_index: int, entry_index: int) -> list:
    """
    Return a deep-copied slice of ``anime_list`` starting from the entry that
    follows ``entry_index`` within ``anime_index``.

    If the current anime has no remaining entries after the slice, the next
    anime in the list is used as the starting point instead.
    """
    remaining = copy.deepcopy(anime_list)
    remaining[anime_index]["entries"] = remaining[anime_index]["entries"][entry_index:]

    if remaining[anime_index]["entries"]:
        return remaining[anime_index:]
    return remaining[anime_index + 1:]


async def _scrape_convert_and_upload(
    entry: dict,
    browser_ctx: BrowserContext,
    session: aiohttp.ClientSession,
    anilist_info: dict,
) -> bool:
    """
    Run the full pipeline for a single episode entry:
      1. Scrape HLS chunks from the provider page.
      2. Merge and convert to MKV.
      3. Upload to Telegram.
      4. Persist the file reference in MongoDB.

    Returns True on success (or when no metadata was returned), False if the
    upload step fails so the caller can decide whether to abort.
    """
    scraper  = Scraper()
    metadata = await scraper.scrape(entry, browser_ctx, session)

    if not metadata:
        logger.info("No metadata received for entry: %s", entry.get("name"))
        return True

    logger.info("Merging & converting to MKV")
    metadata = await convert(metadata)

    logger.info("Sending data to uploader process")
    success, channel_id, msg_id = await _upload_to_telegram(metadata)

    if not success:
        logger.warning("Uploader reported failure for entry: %s", entry.get("name"))
        return False

    # Build search query tokens from the output filename stem.
    queries: list[str] = Path(metadata.video).stem.split("_")
    try:
        await TelegramFile(
            channel_id=channel_id,
            msg_id=msg_id,
            queries=queries,
            anilist_id=anilist_info["info"]["id"],
        ).create()
        logger.info("Saved to DB")
    except Exception:
        logger.error("[mongo] Error saving to DB:")
        print(traceback.format_exc())

    return True


# ---------------------------------------------------------------------------
# Scraping modes
# ---------------------------------------------------------------------------

async def _auto_scraping(ctx: BrowserContext, session: aiohttp.ClientSession) -> None:
    """
    Iterate over a generated (or resumed) anime list and scrape every episode.

    Progress is saved after each successful entry so the job can be resumed
    if interrupted. When the list is exhausted the page counter is incremented.
    """
    tracker = ProgressTracker(RECORD_FILE)
    page, anime_list = await _load_or_generate_anime_list(tracker)

    for i, anime in enumerate(anime_list):
        entries = anime["entries"]
        for j, entry in enumerate(entries):
            logger.info(
                "Scrape job: [entry %s/%s | anime %s/%s]",
                j + 1, len(entries),
                i + 1, len(anime_list),
            )
            success = await _scrape_convert_and_upload(entry, ctx, session, anime)
            if success:
                tracker.save(page, _compute_remaining_track(anime_list, i, j))

    tracker.save(page + 1, None)


async def _requested_scraping(
    ctx: BrowserContext,
    session: aiohttp.ClientSession,
    request: RequestedMode,
) -> bool:
    """
    Scrape a single user-requested episode.

    Returns False if the anime could not be found or the provider is unsupported.
    """
    if request.provider != "anikoto":
        logger.warning("Unsupported provider: %s", request.provider)
        return False

    anime_info = await Anilist().find(ani_id=request.anilist_id)
    if not anime_info:
        logger.warning("Anilist entry not found for ID: %s", request.anilist_id)
        return False

    entry = URLBuilder().build_episode_entry(anime_info, request.episode, request.content_type)
    return await _scrape_convert_and_upload(entry, ctx, session, anime_info)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def scrape_job() -> None:
    """
    Main scraping loop.

    Sets up the proxy server and browser, then continuously polls the scrape
    queue and dispatches to either automatic or requested scraping mode.
    """
    _clear_temp()
    await init_mongo()

    server = ProxyServer()
    server.launch()

    async with (
        AsyncCamoufox(
            headless=True,
            firefox_user_prefs={"media.volume_scale": "0.0"},
        ) as browser,
        aiohttp.ClientSession() as session,
    ):
        ctx = await browser.new_context()

        while True:
            if app_ctx.scrape_q.empty():
                await asyncio.sleep(1)
                continue

            item: ScrapingRequests = app_ctx.scrape_q.get_nowait()

            if item.mode == "auto":
                await _auto_scraping(ctx, session)
            else:
                await _requested_scraping(ctx, session, item)