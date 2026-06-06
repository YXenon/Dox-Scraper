"""
scrapers/anikoto.py
===================
Scraper implementation for the Anikoto provider (via megaplay.buzz).

Provider details
----------------
- Stream URLs are routed through a local proxy at ``http://localhost:8280``
  so Camoufox can intercept and forward HLS requests.
- Content is identified by an ``iframe#scrap`` element; absence of that
  element, or the presence of ``.error-container`` inside it, signals an
  invalid / unavailable episode.
"""

import traceback
from urllib.parse import quote

import aiohttp
from camoufox.async_api import BrowserContext  # type: ignore
from pathvalidate import sanitize_filename

from scraper.base import BaseScraper, _BASE_PLAYER_WAIT, _MAX_ATTEMPTS, _PLAYER_WAIT_INCREMENT
from scraper.utils import _clear_temp
from shared.models import Metadata

import asyncio

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTENT_TYPES: list[str] = ["sub", "dub"]

PROVIDER_ORIGIN: str = "https://megaplay.buzz/"
PROVIDER: str = f"{PROVIDER_ORIGIN}/stream/ani"


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


class URLBuilder:
    """Builds a flat list of per-anime episode stream entries for Anikoto."""

    def __init__(self, anime_list: list[dict] | None = None) -> None:
        self.anime_list: list[dict] = anime_list if anime_list is not None else []

    def build_episode_entry(
        self,
        anime: dict,
        episode: int | str,
        content_type: str,
        url: str | None = None,
    ) -> dict[str, str | int]:
        """
        Construct a single episode entry dict.

        If ``url`` is provided it is used as-is; otherwise the URL is built
        from the provider base URL, anime id, episode number, and content type.

        Parameters
        ----------
        anime:
            Anime dict containing at least ``"id"``, ``"title.english"``,
            and ``"episodes"``.
        episode:
            Episode number, or ``""`` / ``0`` for a standalone (non-episodic) entry.
        content_type:
            ``"sub"`` or ``"dub"``.
        url:
            Optional override URL. When omitted the URL is constructed automatically.

        Returns
        -------
        dict
            Entry dict compatible with ``BaseScraper.scrape()``.
        """
        anime_id = anime["id"]
        sanitized_title = sanitize_filename(anime["title"]["english"]).replace(" ", "_")

        if episode in ("", 0):
            name = f"{anime_id}_{sanitized_title}_{content_type}"
        elif content_type == "":
            name = f"{anime_id}_{sanitized_title}_episode_{episode}"
        else:
            name = f"{anime_id}_{sanitized_title}_episode_{episode}_{content_type}"

        return {
            "name": name,
            "url": url or f"{PROVIDER}/{anime_id}/{episode}/{content_type}",
            "episode": episode,
            "content_type": content_type,
            "provider": "anikoto",
        }

    def build(self) -> list[list[dict]]:
        """
        Generate all sub/dub episode entries grouped by anime.
        Anime with a falsy episode count (``None``, ``0``, ``""``) are skipped.

        Returns
        -------
        list[list[dict]]
            Outer list: one element per anime.
            Inner list: one entry per (episode × content_type) combination.
        """
        entries: list[list[dict]] = []

        for anime in self.anime_list:
            if not anime["episodes"]:
                continue

            anime_entries = [
                self.build_episode_entry(anime, episode, content_type)
                for episode in range(1, int(anime["episodes"]) + 1)
                for content_type in CONTENT_TYPES
            ]
            entries.append(anime_entries)

        return entries


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


class Scraper(BaseScraper):
    """
    Anikoto-specific scraper.

    Navigates to each episode URL through the local proxy server, waits for
    the HLS player to issue its playlist requests, then delegates chunk
    downloading to the base class.
    """

    PROVIDER_ORIGIN = "https://megaplay.buzz/"

    # ------------------------------------------------------------------
    # Provider-specific helpers
    # ------------------------------------------------------------------

    async def _is_invalid_url(self, frame) -> bool:
        """
        Return ``True`` if the iframe is missing or shows an error overlay.

        Parameters
        ----------
        frame:
            The ``ElementHandle`` for ``iframe#scrap``, or ``None`` if the
            selector timed out.
        """
        if not frame:
            return True
        return (
            await (await frame.content_frame()).query_selector(".error-container")
            is not None
        )

    # ------------------------------------------------------------------
    # Public entry point (implements BaseScraper.scrape)
    # ------------------------------------------------------------------

    async def scrape(
        self,
        target: dict,
        browser_ctx: BrowserContext,
        http_session: aiohttp.ClientSession,
    ) -> Metadata | None:
        """
        Navigate to ``target["url"]`` via the local proxy, capture HLS
        responses, download all TS chunks, and return a ``Metadata`` object.

        The URL is routed through ``http://localhost:8280`` so Camoufox can
        intercept and forward stream requests.  Each retry adds extra wait
        time (up to ``_MAX_ATTEMPTS * _PLAYER_WAIT_INCREMENT`` seconds) to
        accommodate slower CDNs.

        Parameters
        ----------
        target:
            Entry dict from ``URLBuilder.build_episode_entry()``.
        browser_ctx:
            Live Camoufox ``BrowserContext``.
        http_session:
            Live ``aiohttp.ClientSession`` for chunk downloads.

        Returns
        -------
        Metadata
            Populated on success.
        None
            If the URL is invalid, no media was found, or an error occurred.
        """
        metadata = Metadata()
        self._current_title = target["name"]

        for attempt in range(_MAX_ATTEMPTS):
            try:
                page = await browser_ctx.new_page()
                page.on("response", self._on_browser_response)

                proxied_url = f"http://localhost:8280?url={quote(target['url'])}"
                await page.goto(proxied_url)
                frame = await page.wait_for_selector("iframe#scrap")

                if await self._is_invalid_url(frame):
                    return None

                # Allow the player time to issue playlist requests.
                player_wait = _BASE_PLAYER_WAIT + (_PLAYER_WAIT_INCREMENT * attempt)
                await asyncio.sleep(player_wait)
                await page.close()

                await self._download_chunks(http_session)
                metadata = self._metadata

                if self._media_found:
                    return metadata

                # No video stream detected — clean up the temp directory.
                if metadata.dir:
                    _clear_temp()

            except Exception:
                self._logger.error("[scrape] Error for '%s':", target.get("name"))
                print(traceback.format_exc())
                if metadata.dir:
                    _clear_temp()
                return None