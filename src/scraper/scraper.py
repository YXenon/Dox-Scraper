import asyncio
import subprocess
from pathlib import Path
import typing
import http.server
import threading
import aiofiles
import aiohttp
from aiohttp.typedefs import LooseHeaders
from camoufox.async_api import BrowserContext # type: ignore
from urllib.parse import urlparse, parse_qs, quote
from tqdm import trange
from tqdm.asyncio import tqdm
from os import remove

from scraper.config import PROVIDER_ORIGIN


class PortHandler(http.server.BaseHTTPRequestHandler):
    def _HTML(self, url: str):
        return f"""
                <!DOCTYPE html>
                <html lang="en">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Player</title>
                </head>
                <body style="width: 100dvw; height: 100dvh;">
                    <iframe src="{url}" width="100%" height="100%" frameborder="0" scrolling="no" allowfullscreen muted></iframe>
                </body>
                </html>
        """.encode()

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        target = params.get("url", [""])[0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(self._HTML(target))

    def log_message(self, *args):
        pass  # silence logs


class Server:
    server: http.server.HTTPServer

    def __init__(self) -> None:
        pass

    def launch(self):
        self.server = http.server.HTTPServer(("localhost", 8280), PortHandler)
        self.server.handle_error = lambda *_: None  # type: ignore # suppress BrokenPipeError
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


DEFAULT_METADATA = {"video": "", "subtitles": []}
DEFAULT_FILE_DIR = None

class Scraper:
    media_urls: typing.List[str]
    current_file_dir: Path | None

    def __init__(self) -> None:
        self.media_urls = []
        self.current_file_name = ""
        self.current_file_dir = DEFAULT_FILE_DIR
        self.metadata = DEFAULT_METADATA
        self.media_found = False

    async def _load_m3u8_playlist(self, response):
        url = str(response.url)
        if url.endswith(".m3u8"):
            content = str(await response.text())
            if "#EXTINF" in content:
                for line in content.splitlines():
                    if "https://" in line:
                        self.media_urls.append(line)
        elif url.endswith(".vtt"):
            content = str(await response.text())
            sub_file_name = url.split("/")[-1]
            await self._handle_subtitles(content, sub_file_name)

    def _resolve_dir(self):
        self.current_file_dir = Path("./temp") / self.current_file_name
        self.current_file_dir.mkdir(exist_ok=True, parents=True)

    async def _handle_subtitles(self, content: str, file_name: str):
        self._resolve_dir()
        subtitles_file = (
            self.current_file_dir / f"{self.current_file_name}_{file_name}.vtt"
        )
        async with aiofiles.open(subtitles_file.resolve(), "a") as f:
            await f.write(content)

        self.metadata["subtitles"].append(Path(subtitles_file).as_posix())

    async def _fetch_urls_and_write(self, session: aiohttp.ClientSession):
        headers: LooseHeaders = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Origin": PROVIDER_ORIGIN,
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Referer": PROVIDER_ORIGIN,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }

        urls = self.media_urls
        if len(urls) > 0:
            self._resolve_dir()
            self.media_found = True
        else:
            return
        
        file_path = self.current_file_dir / f"{self.current_file_name}.ts"
        sem = asyncio.Semaphore(75)

        async def temp_fetch_and_write(url: str, multiplier: int, bar: tqdm):
            try:
                async with (
                    aiofiles.open(
                        (self.current_file_dir / f"temp_{multiplier}.ts"), "w+b"
                    ) as file,
                    sem,
                ):
                    response = await session.get(url, headers=headers)
                    data = await response.read()
                    await file.write(data)
                    await file.flush()
                    await file.close()

            except Exception as e:
                print(e)

            bar.update(1)

        with tqdm(total=len(urls), unit="chunks", desc="=> Downloading ") as bar:
            await asyncio.gather(
                *(
                    asyncio.create_task(temp_fetch_and_write(url, i, bar))
                    for i, url in enumerate(urls)
                )
            )

        with open(file_path, "ab") as file:
            for i in trange(len(urls), desc="=> Merging ", unit="file"):
                temp_file_path = (self.current_file_dir / f"temp_{i}.ts")
                with open(
                    temp_file_path.resolve(), "rb"
                ) as temp:
                    data = temp.read()
                    file.write(data)
        # print("=> Merging files")
        # temp_files = [(self.current_file_dir / f"temp_{i}.ts").as_posix() for i in range(len(urls))]
        # with open(file_path, "wb") as out:
        #     subprocess.run(["cat", *temp_files], stdout=out, check=True)

        self.metadata["video"] = file_path.as_posix()

    async def _cleanup(self):
        if self.media_found and self.current_file_dir:
            for i in range(len(self.media_urls)):
                remove(self.current_file_dir / f"temp_{i}.ts")
        self.media_urls.clear()
        self.media_found = False
        self.current_file_dir = DEFAULT_FILE_DIR
        self.metadata = DEFAULT_METADATA

    async def _request_interception(self, response):
        await self._load_m3u8_playlist(response)

    async def scrape(self, url: dict, ctx: BrowserContext, client_session: aiohttp.ClientSession):
        try:
            self.current_file_name = url["name"]
            page = await ctx.new_page()
            page.on("response", self._request_interception)

            await page.goto(f"http://localhost:8280?url={quote(url["url"])}")
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(5)
            await page.close()

            await self._fetch_urls_and_write(client_session)

            metadata = self.metadata
            found = self.media_found

            await self._cleanup()

            if found:
                return metadata
            return None
        except Exception as e:
            print(e)
            return None
