import asyncio
import copy
from itertools import chain
import logging
import shutil
import traceback
from pathlib import Path
from typing import Any

import aiohttp
from camoufox import AsyncCamoufox
from playwright.async_api import BrowserContext

from shared.models import (
    Metadata,
    AnimeMode,
    RequestedMode,
    ScrapingRequests,
    TelegramFile,
    ScrapedAnime,
)
from shared.mongo import init_mongo

from .anilist import Anilist
from .converter import convert
from .progress import ProgressTracker
from .proxy import ProxyServer
from .providers.anikoto import URLBuilder, Scraper
from core.handlers.process import app_ctx

TEMP_DIR = Path("./temp")
RECORD_FILE = Path("record.json")
CONTENT_TYPES = ("sub", "dub")

logger = logging.getLogger(__name__)


def _clear_temp() -> None:
    try:
        shutil.rmtree(TEMP_DIR)
    except FileNotFoundError:
        pass


async def _load_or_generate_anime_list(
    tracker: ProgressTracker,
) -> tuple[int, int, list]:
    page, index, items = tracker.load()
    if items:
        return page, index, items

    raw_anime_list = await Anilist().generate(page, 1)
    grouped_entries = URLBuilder(anime_list=raw_anime_list).build()

    anime_list = [
        {"info": data, "entries": entry}
        for data, entry in zip(raw_anime_list, grouped_entries)
    ]
    return page, index, anime_list


async def _upload_to_telegram(metadata: Metadata) -> tuple[bool, Any, Any]:
    loop = asyncio.get_event_loop()
    app_ctx.scraper_conn.send(metadata.model_dump_json())
    response: dict = await loop.run_in_executor(None, app_ctx.scraper_conn.recv)

    success = response.get("job") == "upload" and response.get("status") == "done"
    return success, response.get("channel_id"), response.get("msg_id")


def _compute_remaining_track(
    anime_list: list, anime_index: int, entry_index: int
) -> list:
    remaining = copy.deepcopy(anime_list)
    remaining[anime_index]["index"] = entry_index
    return remaining


def _queries(metadata: Metadata, anilist_info: dict) -> list[str]:
    file_name_queries = Path(metadata.video).stem.split("_")
    synonym_queries = list(
        chain.from_iterable(
            synonym.split(" ") for synonym in anilist_info["info"]["synonyms"]
        )
    )
    return file_name_queries + synonym_queries


async def _scrape_convert_and_upload(
    entry: dict,
    browser_ctx: BrowserContext,
    session: aiohttp.ClientSession,
    anilist_info: dict,
    anime_doc: ScrapedAnime
) -> bool:
    scraper = Scraper()
    metadata = await scraper.scrape(entry, browser_ctx, session)
    if not metadata:
        logger.info("No metadata for entry: %s", entry.get("name"))
        return False

    logger.info("Merging & converting to MKV")
    metadata = await convert(metadata)

    logger.info("Sending to uploader")
    success, channel_id, msg_id = await _upload_to_telegram(metadata)

    if not success:
        logger.warning("Upload failed for: %s", entry.get("name"))
        return False

    try:
        await TelegramFile(
            channel_id=channel_id,
            msg_id=msg_id,
            queries=_queries(metadata, anilist_info),
            anilist_id=anilist_info["info"]["id"],
        ).create()
        await anime_doc.mark_scraped(
            episode=entry["episode"],
            content_type=entry.get("content_type", "sub"),
        )
        logger.info("Saved to DB")
    except Exception:
        logger.error("[mongo] Error saving to DB:\n%s", traceback.format_exc())

    return True


# ---------------------------------------------------------------------------
# Scraping modes
# ---------------------------------------------------------------------------


async def _auto_scraping(ctx: BrowserContext, session: aiohttp.ClientSession) -> None:

    logger.info("Auto scraping requested")

    tracker = ProgressTracker(RECORD_FILE)
    page, index, anime_list = await _load_or_generate_anime_list(tracker)

    for i, anime in enumerate(anime_list[index:]):

        anime_info = anime["info"]
        is_airing = anime_info.get("status") == "RELEASING"

        anime_doc = await ScrapedAnime.get_or_create(
            anilist_id=anime_info["id"],
            title=anime_info["title"].get("english", ""),
            is_airing=is_airing,
            anime_info=anime_info,
        )

        if anime_doc is None:
            logger.warning(
                "Could not resolve episode count for anilist_id=%s, skipping",
                anime_info["id"],
            )
            return
        elif all(anime_doc.is_complete(content_type) for content_type in CONTENT_TYPES):
            return

        entries = anime["entries"]
        entry_index = anime.get("index", 0)
        for j, entry in enumerate(entries[entry_index:]):

            logger.info(
                "Auto scrape: [ep %s/%s | anime %s/%s]",
                j + 1,
                len(entries[entry_index:]),
                i + 1,
                len(anime_list[index:]),
            )
            success = await _scrape_convert_and_upload(entry, ctx, session, anime, anime_doc)
            tracker.save(
                page,
                _compute_remaining_track(anime_list, i, entry_index + j + 1),
                i,
            )
            if not success:
                await anime_doc.mark_failed(int(entry["episode"]), str(entry["content_type"]))

        tracker.save(page, anime_list, index + i + 1)

    tracker.save(page + 1, None, 0)


async def _single_scrape(
    ctx: BrowserContext,
    session: aiohttp.ClientSession,
    request: RequestedMode,
) -> bool:
    """Scrape one specific episode. URL is built automatically if not provided."""
    logger.info("Single episode scrape requested (provider=%s)", request.provider)

    if request.provider != "anikoto":
        logger.warning("Unsupported provider: %s", request.provider)
        return False

    anime_info = await Anilist().find(ani_id=request.anilist_id)
    if not anime_info:
        logger.warning("Anilist entry not found: %s", request.anilist_id)
        return False

    entry = URLBuilder().build_episode_entry(
        anime_info,
        request.episode,
        request.content_type,
        url=request.url or None,  # None → URLBuilder constructs it
    )

    is_airing = anime_info.get("status") == "RELEASING"

    anime_doc = await ScrapedAnime.get_or_create(
        anilist_id=request.anilist_id,
        title=anime_info["title"].get("english", ""),
        is_airing=is_airing,
        anime_info=anime_info,
    )

    if anime_doc is None:
        logger.warning(
            "Could not resolve episode count for anilist_id=%s, skipping",
            request.anilist_id,
        )
        return False
    elif request.episode in anime_doc.scraped(request.content_type):
        return True

    ok = await _scrape_convert_and_upload(entry, ctx, session, {"info": anime_info}, anime_doc)
    if not ok:
        await anime_doc.mark_failed(int(entry["episode"]), str(entry["content_type"]))
        return False

    return True


async def _anime_scrape(
    ctx: BrowserContext,
    session: aiohttp.ClientSession,
    request: AnimeMode,
) -> None:
    """
    Scrape all available (or missing) episodes for one anime.
    Checks ScrapedAnime to skip already-done episodes.
    """
    logger.info("Full anime scrape requested (id=%s, provider=%s)", request.anilist_id, request.provider)

    if request.provider != "anikoto":
        logger.warning("Unsupported provider: %s", request.provider)
        return

    anime_info = await Anilist().find(ani_id=request.anilist_id)
    if not anime_info:
        logger.warning("Anilist entry not found: %s", request.anilist_id)
        return

    is_airing = anime_info.get("status") == "RELEASING"

    anime_doc = await ScrapedAnime.get_or_create(
        anilist_id=request.anilist_id,
        title=anime_info["title"].get("english", ""),
        is_airing=is_airing,
        anime_info=anime_info,
    )

    if anime_doc is None:
        logger.warning(
            "Could not resolve episode count for anilist_id=%s, skipping",
            request.anilist_id,
        )
        return

    # Retry previously failed episodes first, then missing ones
    entries = []
    for content_type in CONTENT_TYPES:
        failed = list(anime_doc.failed(content_type))
        missing = anime_doc.missing_episodes(content_type)
        episodes_to_scrape = sorted(set(failed + missing))

        entries += [
            URLBuilder().build_episode_entry(anime_info, ep, content_type)
            for ep in episodes_to_scrape
        ]

    if not entries:
        logger.info(
            "Nothing to scrape for anilist_id=%s (%s)",
            request.anilist_id,
            content_type,
        )
        return

    for i, entry in enumerate(entries):
        logger.info("Anime scraping: (%s/%s)", i+1, len(entries))

        ok = await _scrape_convert_and_upload(entry, ctx, session, {"info": anime_info}, anime_doc)
        if not ok:
            await anime_doc.mark_failed(int(entry["episode"]), str(entry["content_type"]))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def scrape_job() -> None:
    """
    Entrypoint function for scrape job, requests can only be made through the telegram client.
    Currently there are 3 modes - auto, single episode request, full anime request.
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
        try:
            ctx = await browser.new_context() # type: ignore

            while True:
                if app_ctx.scrape_q.empty():
                    await asyncio.sleep(1)
                    continue

                item: ScrapingRequests = app_ctx.scrape_q.get_nowait()

                if item.mode == "auto":
                    await _auto_scraping(ctx, session)
                elif item.mode == "request":
                    await _single_scrape(ctx, session, item)
                elif item.mode == "anime":
                    await _anime_scrape(ctx, session, item)
        finally:
            pass
