"""
shared/base_scraper.py
======================
Abstract base class for all HLS anime scrapers.

Subclasses are expected to:
  1. Set the ``PROVIDER_ORIGIN`` class variable to the provider's origin URL.
  2. Implement the ``scrape()`` method with provider-specific browser navigation.
  3. Optionally override ``_make_subtitle_filename()`` to customise VTT naming.

Shared responsibilities handled here:
  - Browser response interception (HLS + VTT).
  - TS chunk collection, concurrent download, and progress reporting.
  - Subtitle persistence.
  - Output-directory lifecycle management.
"""

import asyncio
import re
import traceback
from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles
import aiohttp
from camoufox.async_api import BrowserContext  # type: ignore
from tqdm.asyncio import tqdm

from shared.logger import log
# from shared.models import Metadata

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Maximum concurrent TS chunk downloads per scrape session.
_MAX_CONCURRENT_DOWNLOADS: int = 75

# Maximum page-load / scrape attempts before giving up on a single target.
_MAX_ATTEMPTS: int = 20

# Base wait time (seconds) before declaring the player has fired its requests.
# Each retry adds an extra 0.5 s to accommodate slower servers.
_BASE_PLAYER_WAIT: float = 5.0
_PLAYER_WAIT_INCREMENT: float = 0.5


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class BaseScraper(ABC):
    """
    Abstract base for all provider-specific HLS scrapers.

    Lifecycle
    ---------
    1. Caller invokes ``scrape(target, browser_ctx, http_session)``.
    2. The subclass navigates to the provider URL and registers
       ``_on_browser_response`` as a Playwright response listener.
    3. ``_handle_m3u8_or_vtt`` collects chunk URLs and saves subtitle files.
    4. ``_download_chunks`` fetches all TS segments concurrently.
    5. A populated ``Metadata`` object is returned on success; ``None`` on failure.

    Class variables
    ---------------
    PROVIDER_ORIGIN : str
        The scheme + host of the provider (e.g. ``"https://example.com"``).
        Used as ``Origin`` and ``Referer`` in every outbound HTTP request.
    """

    PROVIDER_ORIGIN: str = ""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._chunk_urls: list[str] = []
        self._current_title: str = ""
        self._output_dir: Path | None = None
        self._metadata: Metadata = Metadata()
        self._media_found: bool = False
        self._logger = log

    # ------------------------------------------------------------------
    # Request headers (built on demand so PROVIDER_ORIGIN is always current)
    # ------------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        """HTTP headers sent with every TS chunk and subtitle request."""
        return {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Origin": self.PROVIDER_ORIGIN,
            "Referer": self.PROVIDER_ORIGIN,
            "Connection": "keep-alive",
            "Sec-GPC": "1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }

    # ------------------------------------------------------------------
    # Browser response interception
    # ------------------------------------------------------------------

    async def _on_browser_response(self, response) -> None:
        """
        Playwright response callback.

        Registered via ``page.on("response", self._on_browser_response)``.
        Delegates every response to ``_handle_m3u8_or_vtt``.
        """
        await self._handle_m3u8_or_vtt(response)

    def _is_subtitle_vtt(self, content: str) -> bool:
        cues = re.findall(r'-->\s*.+\n(.+)', content)
        url_count = sum(1 for c in cues if re.match(r'https?://', c))
        return bool(cues) and (url_count / len(cues)) < 0.5

    async def _handle_m3u8_or_vtt(self, response) -> None:
        """
        Inspect a browser response and act on recognised media types.

        - ``.m3u8`` — if it is a *media* playlist (contains ``#EXTINF``),
          all ``https://`` lines are appended to ``_chunk_urls``.
          Master playlists (quality selectors) are intentionally skipped.
        - ``.vtt`` — content is persisted to disk via ``_save_subtitle``.

        Exceptions are silently swallowed so that a single bad response
        never aborts the entire capture session.
        """
        url = str(response.url)
        try:
            if url.endswith(".m3u8"):
                content = await response.text()
                if not "#EXTINF" in content:
                    return
                for line in content.splitlines():
                    if line.startswith("https://"):
                        self._chunk_urls.append(line)

            elif url.endswith(".vtt"):
                content = await response.text()
                if not self._is_subtitle_vtt(content):
                    return
                # Derive a stem from the last URL path segment, dropping its extension.
                raw_segment = url.rsplit("/", maxsplit=1)[-1]
                subtitle_stem = "_".join(raw_segment.split(".")[:-1])
                subtitle_filename = self._make_subtitle_filename(subtitle_stem)
                await self._save_subtitle(content, subtitle_filename)

        except Exception:
            pass

    def _make_subtitle_filename(self, subtitle_stem: str) -> str:
        """
        Construct the VTT filename from the URL-derived stem.

        Override in subclasses that need extra uniqueness guarantees
        (e.g. appending a random token to avoid collisions when the same
        subtitle segment is delivered across multiple responses).

        Parameters
        ----------
        subtitle_stem:
            The cleaned filename stem extracted from the response URL.

        Returns
        -------
        str
            A ``.vtt`` filename ready to be combined with the output directory.
        """
        return f"{subtitle_stem}.vtt"

    # ------------------------------------------------------------------
    # Output directory
    # ------------------------------------------------------------------

    def _ensure_output_dir(self) -> None:
        """
        Create ``./temp/<current_title>/`` on first call and record its path
        in ``_metadata.dir``.  Subsequent calls are no-ops.
        """
        if self._output_dir is None:
            self._output_dir = Path("./temp") / self._current_title
            self._output_dir.mkdir(exist_ok=True, parents=True)
            self._metadata.dir = self._output_dir.as_posix()

    # ------------------------------------------------------------------
    # Subtitle persistence
    # ------------------------------------------------------------------

    async def _save_subtitle(self, content: str, filename: str) -> None:
        """
        Append ``content`` to a VTT file inside the output directory.

        Appending (rather than overwriting) correctly handles providers that
        stream a single logical subtitle file across multiple HTTP responses.

        Parameters
        ----------
        content:
            Raw VTT text received from the browser.
        filename:
            Destination filename (no directory prefix).
        """
        self._ensure_output_dir()
        subtitle_path = self._output_dir / f"{self._current_title}_{filename}.vtt"

        async with aiofiles.open(subtitle_path.resolve(), "a") as f:
            await f.write(content)

        self._metadata.subtitles.append(subtitle_path.as_posix())

    # ------------------------------------------------------------------
    # TS chunk download
    # ------------------------------------------------------------------

    async def _download_chunks(self, session: aiohttp.ClientSession) -> None:
        """
        Concurrently download every URL in ``_chunk_urls`` to numbered
        temp files (``temp_0.ts``, ``temp_1.ts``, …).

        A semaphore limits concurrency to ``_MAX_CONCURRENT_DOWNLOADS``.
        Individual chunk failures are logged and retried up to ``_MAX_ATTEMPTS``
        times; they do not abort the overall download.

        On completion, ``_metadata.video`` and ``_metadata.parts`` are populated.

        Parameters
        ----------
        session:
            A live ``aiohttp.ClientSession`` shared across the scrape call.
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
                        response = await session.get(url, headers=self._headers)
                        data = await response.read()
                        if not data:
                            await asyncio.sleep(2)
                            continue
                        async with aiofiles.open(temp_path, "w+b") as f:
                            await f.write(data)
                            await f.flush()

                    progress_bar.update(1)
                    break

                except Exception:
                    pass
                    # self._logger.error("[chunk %s] Download failed:", index)
                    # print(traceback.format_exc())

        with tqdm(total=len(self._chunk_urls), unit="chunks", desc="Downloading") as bar:
            await asyncio.gather(
                *(
                    asyncio.create_task(download_chunk(url, i, bar))
                    for i, url in enumerate(self._chunk_urls)
                )
            )

        output_path = self._output_dir / f"{self._current_title}.mkv"
        self._metadata.video = output_path.as_posix()
        self._metadata.parts = len(self._chunk_urls)

    # ------------------------------------------------------------------
    # Abstract public entry point
    # ------------------------------------------------------------------

    @abstractmethod
    async def scrape(
        self,
        target: dict,
        browser_ctx: BrowserContext,
        http_session: aiohttp.ClientSession,
    ) -> Metadata | None:
        """
        Navigate to the provider page, capture HLS streams and subtitles,
        download all TS chunks, and return a populated ``Metadata`` object.

        Parameters
        ----------
        target:
            Entry dict produced by ``URLBuilder.build_episode_entry()``.
            Must contain at least ``"name"`` and ``"url"``.
        browser_ctx:
            A live Camoufox ``BrowserContext`` used to open new pages.
        http_session:
            A live ``aiohttp.ClientSession`` for TS chunk downloads.

        Returns
        -------
        Metadata
            Populated on success.
        None
            If no media was found or an unrecoverable error occurred.
        """
        ...