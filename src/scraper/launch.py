import asyncio
import copy
import logging
import traceback
from itertools import chain
from pathlib import Path
from typing import Any

import aiohttp
from camoufox import AsyncCamoufox
from playwright.async_api import BrowserContext

from constants import SCRAPE_PROVIDERS
from shared.models import (
    AnimeMode,
    Metadata,
    RequestedMode,
    ScrapedAnime,
    ScrapingRequests,
    TelegramFile,
)
from shared.mongo import init_mongo

from .anilist import Anilist
from .converter import convert
from .progress import ProgressTracker
from .proxy import ProxyServer
from core.handlers.process import app_ctx
from .utils import _clear_temp, _load_provider


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMP_DIR = Path("./temp")
RECORD_FILE = Path("record.json")
CONTENT_TYPES = ("sub", "dub")
DEFAULT_PROVIDER = "anikoto"

logger = logging.getLogger(__name__)


async def _load_or_generate_anime_list(
    tracker: ProgressTracker,
) -> tuple[int, int, list]:
    """
    Return (page, index, anime_list) from saved progress if it exists,
    otherwise fetch a fresh list from AniList and build episode URLs.
    """
    URLBuilder, _ = _load_provider(DEFAULT_PROVIDER)

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
    """
    Send scraped metadata to the Telegram uploader subprocess and await
    confirmation. Returns (success, channel_id, msg_id).
    """
    loop = asyncio.get_running_loop()
    app_ctx.scraper_conn.send(metadata.model_dump_json())
    response: dict = await loop.run_in_executor(None, app_ctx.scraper_conn.recv)

    success = response.get("job") == "upload" and response.get("status") == "done"
    return success, response.get("channel_id"), response.get("msg_id")


def _compute_remaining_track(
    anime_list: list, anime_index: int, entry_index: int
) -> list:
    """
    Return a deep copy of anime_list with the current entry index stamped in,
    so the progress tracker can resume from the exact position later.
    """
    remaining = copy.deepcopy(anime_list)
    remaining[anime_index]["index"] = entry_index
    return remaining


def _build_search_queries(metadata: Metadata, anilist_info: dict) -> list[str]:
    """
    Build search query tokens for a file by combining tokens from the
    video filename stem and all words across every AniList synonym.
    """
    filename_tokens = Path(metadata.video).stem.split("_")
    synonym_tokens = list(
        chain.from_iterable(
            synonym.split(" ") for synonym in anilist_info["info"]["synonyms"]
        )
    )
    return filename_tokens + synonym_tokens


# ---------------------------------------------------------------------------
# Core scrape pipeline
# ---------------------------------------------------------------------------

async def _scrape_convert_and_upload(
    entry: dict,
    browser_ctx: BrowserContext,
    session: aiohttp.ClientSession,
    episode_no: int,
    content_type: str,
    anilist_info: dict,
    anime_doc: ScrapedAnime,
    scraper_cls,
) -> bool:
    """
    Run the full pipeline for a single episode entry:
      1. Scrape raw metadata via the provider's scraper.
      2. Merge streams and convert to MKV.
      3. Upload the result to Telegram.
      4. Persist the TelegramFile record and mark the episode done in MongoDB.

    Returns True on complete success, False on any stage failure.
    """
    scraper = scraper_cls()
    metadata = await scraper.scrape(entry, browser_ctx, session)
    if not metadata:
        logger.info("Scrape returned no metadata for entry: %s", entry.get("name"))
        return False

    logger.info("Merging & converting to MKV")
    metadata = await convert(metadata)
    if not metadata:
        logger.info("Conversion returned no metadata for entry: %s", entry.get("name"))
        return False

    logger.info("Sending to uploader")
    success, channel_id, msg_id = await _upload_to_telegram(metadata)
    if not success:
        logger.warning("Upload failed for: %s", entry.get("name"))
        return False

    try:
        await TelegramFile.new(
            channel_id=channel_id,
            msg_id=msg_id,
            queries=_build_search_queries(metadata, anilist_info),
            episode_no=episode_no,
            content_type=content_type,
            anilist_id=anilist_info["info"]["id"]
        )
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
    """
    Iterate over an AniList-generated anime list and scrape all episodes,
    resuming from the last saved progress checkpoint.
    """
    logger.info("Auto scraping requested")

    _, Scraper = _load_provider(DEFAULT_PROVIDER)
    tracker = ProgressTracker(RECORD_FILE)
    page, index, anime_list = await _load_or_generate_anime_list(tracker)

    # Slice to the resume point once so we don't recompute len() each iteration
    remaining_anime = anime_list[index:]
    total_anime = len(remaining_anime)

    for i, anime in enumerate(remaining_anime):
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
            continue

        if all(anime_doc.is_complete(content_type) for content_type in CONTENT_TYPES):
            continue

        entries = anime["entries"]
        entry_index = anime.get("index", 0)
        pending_entries = entries[entry_index:]
        total_entries = len(pending_entries)

        for j, entry in enumerate(pending_entries):
            logger.info(
                "Auto scrape: [ep %s/%s | anime %s/%s]",
                j + 1,
                total_entries,
                i + 1,
                total_anime,
            )

            # Skip if this episode was already successfully scraped
            if entry["episode"] in anime_doc.scraped(entry["content_type"]):
                success = True
            else:
                success = await _scrape_convert_and_upload(
                    entry, ctx, session, entry["episode"], entry["content_type"],  anime, anime_doc, Scraper
                )

            tracker.save(
                page,
                _compute_remaining_track(anime_list, i, entry_index + j + 1),
                i,
            )
            if not success:
                await anime_doc.mark_failed(
                    int(entry["episode"]), str(entry["content_type"])
                )

        tracker.save(page, anime_list, index + i + 1)

    # All anime on the current page processed — advance to the next
    tracker.save(page + 1, None, 0)


async def _single_scrape(
    ctx: BrowserContext,
    session: aiohttp.ClientSession,
    request: RequestedMode,
) -> bool:
    """Scrape one specific episode. URL is built automatically if not provided."""
    logger.info("Single episode scrape requested (provider=%s)", request.provider)

    if request.provider not in SCRAPE_PROVIDERS:
        logger.warning("Unsupported provider: %s", request.provider)
        return False

    URLBuilder, Scraper = _load_provider(request.provider)

    anime_info = await Anilist().find(ani_id=request.anilist_id)
    if not anime_info:
        logger.warning("Anilist entry not found: %s", request.anilist_id)
        return False

    entry = URLBuilder().build_episode_entry(
        anime_info,
        request.episode,
        request.content_type,
        url=request.url or None,  # None causes URLBuilder to construct the URL
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

    # Re-attempt the episode if done already
    if request.episode in anime_doc.scraped(request.content_type):
        content_type = "sub" if request.content_type == "sub" else "dub"
        anime_doc.scraped(request.content_type).remove(request.episode)
        telegram_doc = await TelegramFile.find(episode_no=request.episode, content_type=content_type, anilist_id=request.anilist_id)
        if telegram_doc:
            await telegram_doc.delete()

    ok = await _scrape_convert_and_upload(
        entry, ctx, session, request.episode, request.content_type, {"info": anime_info}, anime_doc, Scraper
    )
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
    Scrape all available or missing episodes for one anime.
    Failed episodes are retried first, followed by any not yet scraped.
    """
    logger.info(
        "Full anime scrape requested (id=%s, provider=%s)",
        request.anilist_id,
        request.provider,
    )

    if request.provider not in SCRAPE_PROVIDERS:
        logger.warning("Unsupported provider: %s", request.provider)
        return

    URLBuilder, Scraper = _load_provider(request.provider)

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

    # Retry previously failed episodes first, then fill in any missing ones
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
            content_type,  # retains the last value from the loop above
        )
        return

    total_entries = len(entries)
    for i, entry in enumerate(entries):
        logger.info("Anime scraping: (%s/%s)", i + 1, total_entries)

        ok = await _scrape_convert_and_upload(
            entry, ctx, session, entry["episode"], entry["content_type"], {"info": anime_info}, anime_doc, Scraper
        )
        if not ok:
            await anime_doc.mark_failed(
                int(entry["episode"]), str(entry["content_type"])
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def scrape_job() -> None:
    """
    Entry point for the scrape job. Requests arrive via the Telegram client
    and are dispatched to one of three modes: auto, single-episode, or full-anime.
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
        ctx = await browser.new_context()  # type: ignore

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
