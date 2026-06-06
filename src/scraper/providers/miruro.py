"""
scrapers/miruro.py
==================
Scraper implementation for the Miruro provider (miruro.bz).

Provider details
----------------
- Episode URLs are navigated to directly (no local proxy required).
- Language preference is injected into ``localStorage`` before each page
  load so the player selects the correct sub/dub stream automatically.
- Because Miruro falls back to sub when dub is unavailable, the scraper
  verifies the active language via ``localStorage`` after navigation and
  returns ``None`` on a mismatch rather than silently saving the wrong track.
- Subtitle filenames are suffixed with a 10-character random token to avoid
  collisions when the same VTT segment is delivered across multiple responses.
"""

import asyncio
import traceback
from secrets import choice
from string import ascii_letters
from urllib.parse import quote

import aiohttp
from camoufox.async_api import BrowserContext  # type: ignore
from pathvalidate import sanitize_filename
from playwright.async_api import Page

from scraper.base import BaseScraper, _BASE_PLAYER_WAIT, _MAX_ATTEMPTS, _PLAYER_WAIT_INCREMENT
from scraper.utils import _clear_temp
from shared.models import Metadata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTENT_TYPES: list[str] = ["sub", "dub"]

PROVIDER_ORIGIN: str = "https://miruro.bz"
PROVIDER: str = f"{PROVIDER_ORIGIN}/watch/"

MIRURO_PLAYER_WAIT = 5
# Miruro doesn't load files very easily, so we have to wait for a lil while longer for all the files, this issue is Miruro only, so other providers are unaffected.

# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


class URLBuilder:
    """Builds a flat list of per-anime episode stream entries for Miruro."""

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
        from the provider base URL, AniList id, romanised title, and episode
        number as a query parameter.

        Parameters
        ----------
        anime:
            Anime dict containing at least ``"id"``, ``"title.english"``,
            ``"title.romaji"``, and ``"episodes"``.
        episode:
            Episode number, or ``""`` / ``0`` for a standalone (non-episodic) entry.
        content_type:
            ``"sub"`` or ``"dub"``.
        url:
            Optional override URL. When omitted the URL is constructed automatically.

        Returns
        -------
        dict
            Entry dict compatible with ``BaseScraper.scrape()``, including the
            ``"anilist_id"`` key required for localStorage language injection.
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
            "url": url or f"{PROVIDER}/{anime['id']}/{quote(anime['title']['romaji'])}?ep={episode}",
            "episode": episode,
            "content_type": content_type,
            "provider": "miruro",
            "anilist_id": anime["id"],
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
    Miruro-specific scraper.

    Injects localStorage settings before each page load so the player
    auto-selects the correct language track, then delegates interception
    and chunk downloading to the base class.
    """

    PROVIDER_ORIGIN = "https://miruro.bz"

    # ------------------------------------------------------------------
    # Subtitle filename override
    # ------------------------------------------------------------------

    def _make_subtitle_filename(self, subtitle_stem: str) -> str:
        """
        Append a 10-character random alphabetic token to the subtitle stem.

        Miruro can deliver the same logical VTT segment across multiple
        responses; the random suffix prevents later chunks from overwriting
        earlier ones.

        Parameters
        ----------
        subtitle_stem:
            The cleaned filename stem extracted from the response URL.

        Returns
        -------
        str
            A uniquified ``.vtt`` filename.
        """
        token = "".join(choice(ascii_letters) for _ in range(10))
        return f"{subtitle_stem}_{token}.vtt"

    # ------------------------------------------------------------------
    # Provider-specific helpers
    # ------------------------------------------------------------------

    async def _is_content_type(
        self, page: Page, anilist_id: int, content_type: str
    ) -> bool:
        """
        Verify that the player's active language matches ``content_type``.

        Reads ``miruro:anime:language:<anilist_id>`` from the page's
        localStorage and compares it (case-insensitively) to the expected
        value.  Returns ``False`` if the key is absent or mismatched.

        Parameters
        ----------
        page:
            The Playwright ``Page`` object after navigation.
        anilist_id:
            AniList numeric id used as part of the localStorage key.
        content_type:
            The expected language string (e.g. ``"ssub"`` or ``"dub"``).
        """
        eval_content_type: str | None = await page.evaluate(
            f"localStorage.getItem('miruro:anime:language:{anilist_id}')"
        )
        return bool(
            eval_content_type
            and eval_content_type.lower() == f'"{content_type}"'
        )

    async def _is_correct_episode(self, page: Page, episode_no: int):
        return page.url.endswith(f"?ep={episode_no}")

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
        Navigate to ``target["url"]``, capture HLS responses, download all
        TS chunks, and return a ``Metadata`` object.

        localStorage is pre-seeded via an init script so the player boots
        with the correct language and provider settings.  After navigation
        the active language is verified; a mismatch (e.g. Miruro falling back
        to sub because dub is unavailable) causes an immediate ``None`` return.

        Parameters
        ----------
        target:
            Entry dict from ``URLBuilder.build_episode_entry()``.
            Must include ``"anilist_id"`` and ``"content_type"``.
        browser_ctx:
            Live Camoufox ``BrowserContext``.
        http_session:
            Live ``aiohttp.ClientSession`` for chunk downloads.

        Returns
        -------
        Metadata
            Populated on success.
        None
            If content type mismatches, no media was found, or an error occurred.
        """
        metadata = Metadata()
        self._current_title = target["name"]

        # Miruro uses "ssub" internally for soft-subtitled content.
        content_type = "ssub" if target["content_type"] == "sub" else "dub"

        for attempt in range(_MAX_ATTEMPTS):
            try:
                await browser_ctx.add_init_script(
                    f"""
                        let raw = localStorage.getItem('miruro:settings:user');
                        const settings = raw ? JSON.parse(raw) : {{}};

                        let raw = localStorage.getItem('vds-player');
                        const player = raw ? JSON.parse(raw) : {{}};


                        localStorage.setItem(
                            'miruro:anime:language:{target["anilist_id"]}',
                            '"{content_type}"'
                        );
                        localStorage.setItem(
                            'miruro:settings:user',
                            JSON.stringify({{
                                ...settings,
                                autoPlay: true,
                                defaultProvider: 'bee',
                                langDefault: '{target["content_type"]}'
                            }})
                        );

                        localStorage.setItem(
                        'vds-player',
                        JSON.stringify({{
                            ...player,
                            captions: true
                        }})
                        )
                    """
                )

                page = await browser_ctx.new_page()
                page.on("response", self._on_browser_response)

                await page.goto(target["url"])
                await page.wait_for_load_state("domcontentloaded")

                if not (await self._is_content_type(
                    page, target["anilist_id"], content_type
                ) and await self._is_correct_episode(page, target["episode"])):
                # Miruro falls back to sub if the asked content type is not available.
                    print("Not matching type")
                    return None

                # Allow the player time to issue playlist requests.
                player_wait = _BASE_PLAYER_WAIT + MIRURO_PLAYER_WAIT + (_PLAYER_WAIT_INCREMENT * attempt)
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