import asyncio
import logging
import shutil
import traceback
from pathlib import Path
from typing import Union
from urllib.parse import quote

import aiofiles
import aiohttp
from camoufox.async_api import BrowserContext  # type: ignore
from pathvalidate import sanitize_filename
from tqdm.asyncio import tqdm

from shared.models import Metadata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTENT_TYPES:   list[str] = ["sub", "dub"]
PROVIDER_ORIGIN: str       = "https://megaplay.buzz/"
PROVIDER:        str       = f"{PROVIDER_ORIGIN}/stream/ani"

# Maximum concurrent TS chunk downloads per scrape session.
_MAX_CONCURRENT_DOWNLOADS = 75

# Maximum page-load attempts before giving up on a single target.
_MAX_ATTEMPTS = 20

# Base wait time (seconds) before declaring the player has fired its requests.
# Each retry adds an extra 0.5 s to allow slower servers more time.
_BASE_PLAYER_WAIT = 5.0
_PLAYER_WAIT_INCREMENT = 0.5

# ---------------------------------------------------------------------------
# URLBuilder
# ---------------------------------------------------------------------------

class URLBuilder:
    """Builds a flat list of per-anime episode stream entries."""

    def __init__(self, anime_list: list[dict] | None = None) -> None:
        # Avoid the mutable-default-argument pitfall by defaulting to None.
        self.anime_list: list[dict] = anime_list if anime_list is not None else []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def build_episode_entry(
        self,
        anime: dict,
        episode: Union[int, str],
        content_type: str,
    ) -> dict[str, str]:
        """
        Construct a single episode entry.

        The title is sanitized and spaces are replaced with underscores so the
        resulting name is safe to use as a filename or URL segment.
        """
        anime_id        = anime["id"]
        sanitized_title = sanitize_filename(anime["title"]["english"]).replace(" ", "_")
        if episode in ('', 0):
            name            = f"{anime_id}_{sanitized_title}_{content_type}"
        elif content_type=='':
            name            = f"{anime_id}_{sanitized_title}_episode_{episode}"
        else:
            name            = f"{anime_id}_{sanitized_title}_episode_{episode}_{content_type}"

        url             = f"{PROVIDER}/{anime_id}/{episode}/{content_type}"
        return {"name": name, "url": url}

    def build(self) -> list[list[dict[str, str]]]:
        """
        Generate all sub/dub episode entries grouped by anime.

        Skips any anime whose episode count is falsy (None, 0, empty string).

        Returns
        -------
        entries         : List of per-anime lists, each containing
                          ``{"name": ..., "url": ...}`` dicts.
        PROVIDER_ORIGIN : Base origin URL of the streaming provider.
        """
        entries: list[list[dict[str, str]]] = []

        for anime in self.anime_list:
            if not anime["episodes"]:
                continue

            anime_entries: list[dict[str, str]] = [
                self.build_episode_entry(anime, episode, content_type)
                for episode in range(1, int(anime["episodes"]) + 1)
                for content_type in CONTENT_TYPES
            ]
            entries.append(anime_entries)

        return entries



class Scraper:
    """
    Intercepts HLS (.m3u8) streams and subtitle (.vtt) files loaded by a
    Camoufox browser page, downloads all TS chunks in parallel, and records
    metadata about the resulting files for downstream processing.
    """

    def __init__(self) -> None:
        self._chunk_urls:    list[str]   = []
        self._current_title: str         = ""
        self._output_dir:    Path | None = None
        self._metadata:      Metadata    = Metadata()
        self._media_found:   bool        = False
        self._headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Origin": PROVIDER_ORIGIN,
            "Referer": PROVIDER_ORIGIN,
            "Connection": "keep-alive",
            "Sec-GPC": "1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
    }
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Browser response interception
    # ------------------------------------------------------------------

    async def _on_browser_response(self, response) -> None:
        """Route every captured network response to the appropriate handler."""
        await self._handle_m3u8_or_vtt(response)

    async def _handle_m3u8_or_vtt(self, response) -> None:
        """
        Parse HLS media playlist responses to collect TS chunk URLs,
        or persist subtitle (VTT) responses to disk.

        Only media playlists (those containing ``#EXTINF`` tags) are
        processed; master playlists that list quality levels are ignored.
        """
        url = str(response.url)

        if url.endswith(".m3u8"):
            content = await response.text()
            if "#EXTINF" in content:
                for line in content.splitlines():
                    if line.startswith("https://"):
                        self._chunk_urls.append(line)

        elif url.endswith(".vtt"):
            content = await response.text()
            # Derive a clean filename from the last URL path segment,
            # stripping the extension before appending .vtt.
            raw_segment     = url.rsplit("/", maxsplit=1)[-1]
            subtitle_stem   = "_".join(raw_segment.split(".")[:-1])
            subtitle_filename = f"{subtitle_stem}.vtt"
            await self._save_subtitle(content, subtitle_filename)

    # ------------------------------------------------------------------
    # Subtitle handling
    # ------------------------------------------------------------------

    def _ensure_output_dir(self) -> None:
        """Create the per-title temp directory if it does not exist yet."""
        if self._output_dir is None:
            self._output_dir = Path("./temp") / self._current_title
            self._output_dir.mkdir(exist_ok=True, parents=True)

    async def _save_subtitle(self, content: str, filename: str) -> None:
        """
        Append subtitle content to a VTT file inside the output directory.

        Appending (rather than overwriting) handles cases where a single
        subtitle file is streamed in multiple response chunks.
        """
        self._ensure_output_dir()
        subtitle_path = self._output_dir / f"{self._current_title}_{filename}.vtt"
        self._metadata.dir = self._output_dir.as_posix()

        async with aiofiles.open(subtitle_path.resolve(), "a") as f:
            await f.write(content)

        self._metadata.subtitles.append(subtitle_path.as_posix())

    # ------------------------------------------------------------------
    # Chunk download and merge
    # ------------------------------------------------------------------

    async def _download_chunks(self, session: aiohttp.ClientSession) -> None:
        """
        Download all collected TS chunk URLs concurrently and write each to a
        numbered temp file (``temp_0.ts``, ``temp_1.ts``, …).

        A semaphore caps concurrency at ``_MAX_CONCURRENT_DOWNLOADS`` to avoid
        overwhelming the CDN. Failed chunks are logged but do not abort the
        overall download.
        """
        if not self._chunk_urls:
            return

        self._ensure_output_dir()
        self._media_found = True

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_DOWNLOADS)

        async def download_chunk(url: str, index: int, progress_bar: tqdm) -> None:
            temp_path = self._output_dir / f"temp_{index}.ts"
            for _ in range(_MAX_ATTEMPTS):
                try:
                    async with semaphore:
                        async with aiofiles.open(temp_path, "w+b") as f:
                            response = await session.get(url, headers=self._headers)
                            data     = await response.read()
                            await f.write(data)
                            await f.flush()

                    progress_bar.update(1)
                    break
                except Exception:
                    self._logger.error("[chunk %s] Download failed:", index)
                    print(traceback.format_exc())

        with tqdm(total=len(self._chunk_urls), unit="chunks", desc="Downloading") as bar:
            await asyncio.gather(
                *(
                    asyncio.create_task(download_chunk(url, i, bar))
                    for i, url in enumerate(self._chunk_urls)
                )
            )

        output_path              = self._output_dir / f"{self._current_title}.mkv"
        self._metadata.video     = output_path.as_posix()
        self._metadata.parts     = len(self._chunk_urls)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def scrape(
        self,
        target: dict,
        browser_ctx: BrowserContext,
        http_session: aiohttp.ClientSession,
    ) -> Metadata | None:
        """
        Navigate to ``target["url"]`` through the local proxy server, capture
        HLS responses, download all TS chunks, and return a Metadata object.

        The URL is routed through ``http://localhost:8280`` so Camoufox can
        intercept and forward stream requests. Each retry adds extra wait time
        to accommodate slower servers.

        Returns None if no media was detected or an unrecoverable error occurs.
        """
        metadata = Metadata()

        for attempt in range(_MAX_ATTEMPTS):
            try:
                self._current_title = target["name"]

                page = await browser_ctx.new_page()
                page.on("response", self._on_browser_response)

                proxied_url = f"http://localhost:8280?url={quote(target['url'])}"
                await page.goto(proxied_url)
                await page.wait_for_load_state("domcontentloaded")

                # Allow the player time to issue playlist requests.
                # Wait time grows with each retry to handle slow servers.
                player_wait = _BASE_PLAYER_WAIT + (_PLAYER_WAIT_INCREMENT * attempt)
                await asyncio.sleep(player_wait)
                await page.close()

                await self._download_chunks(http_session)
                metadata = self._metadata

                if self._media_found:
                    return metadata

                # No video stream detected; remove any temp directory.
                if metadata.dir:
                    shutil.rmtree(metadata.dir)
                return None

            except Exception:
                self._logger.error("[scrape] Error for '%s':", target.get("name"))
                print(traceback.format_exc())
                if metadata.dir:
                    shutil.rmtree(metadata.dir)
                return None