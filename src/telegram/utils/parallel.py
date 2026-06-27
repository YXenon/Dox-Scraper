# Inspired by https://github.com/tulir mautrix project.
# Added features: smarter chunk handling, non-rate-limiting progress callbacks,
# and parallel multi-DC connection management.
# Note: Claude has been used only to make the code more readable,
# all the functionalities were written by https://github.com/YXenon

import asyncio
import hashlib
import logging
from math import ceil
from os import replace, listdir
from os.path import getsize
from pathlib import Path
import traceback
from typing import Callable, Awaitable, Any

import aiofiles
from pathvalidate import sanitize_filename
from telethon import TelegramClient, helpers
from telethon.network import MTProtoSender
from telethon.tl.custom.file import File
from telethon.tl.custom.message import Message
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import GetFileRequest, SaveFilePartRequest, SaveBigFilePartRequest
from telethon.tl.alltlobjects import LAYER
from telethon.tl.types import InputDocumentFileLocation, Document, InputFile, InputFileBig
from telethon.utils import get_appropriated_part_size, get_input_location
from tqdm.asyncio import tqdm

from .crypto_str import crypt

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int], Awaitable[Any]] | None

# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages a pool of MTProto senders connected to a specific DC."""

    def __init__(self, client: TelegramClient, dc_id: int) -> None:
        self.client = client
        self.dc_id = dc_id
        self.senders: list[MTProtoSender] = []
        self.dc = None
        self.auth_key = None

    async def init(self) -> None:
        """Resolve DC metadata and set auth key if already authenticated to this DC."""
        self.dc = await self.client._get_dc(self.dc_id)
        # Reuse the existing auth key only when the session targets the same DC.
        session_matches_dc = self.client.session.dc_id == self.dc_id
        self.auth_key = self.client.session.auth_key if session_matches_dc else None  # type: ignore

    async def _create_sender(self) -> MTProtoSender:
        """
        Open one MTProto connection to the target DC.

        The first cross-DC sender performs an authorization export/import so
        subsequent senders can share the same auth key without repeating that
        round-trip.
        """
        sender = MTProtoSender(auth_key=self.auth_key, loggers=self.client._log)
        dc = self.dc
        client = self.client

        await sender.connect(
            client._connection(
                ip=dc.ip_address,
                port=dc.port,
                dc_id=dc.id,
                loggers=client._log,
                proxy=client._proxy,
            )
        )

        # If we have no auth key yet, export from the home DC and import here.
        if not self.auth_key:
            auth = await client(ExportAuthorizationRequest(self.dc_id))
            client._init_request.query = ImportAuthorizationRequest(  # type: ignore
                id=auth.id, bytes=auth.bytes
            )
            await sender.send(InvokeWithLayerRequest(LAYER, client._init_request))  # type: ignore
            # Cache so all further senders on this DC share the key.
            self.auth_key = sender.auth_key

        self.senders.append(sender)
        return sender

    async def create_connections(self, count: int) -> None:
        """Open `count` parallel senders, initialising the DC if needed."""
        if self.dc is None:
            await self.init()

        # First sender handles auth export/import; the rest can run in parallel.
        await self._create_sender()
        await asyncio.gather(*(self._create_sender() for _ in range(count - 1)))

    async def disconnect_all(self) -> None:
        """Gracefully disconnect every open sender."""
        for sender in self.senders:
            await sender.disconnect()
        self.senders.clear()

# ---------------------------------------------------------------------------
# CommonManager  (shared state for upload and download)
# ---------------------------------------------------------------------------

class CommonManager:
    """Base class holding shared configuration for file transfers."""

    def __init__(
        self,
        client: TelegramClient,
        size: int,
        progress_callback: ProgressCallback = None,
    ) -> None:
        self.client = client
        self.size = size
        # get_appropriated_part_size returns kB; convert to bytes.
        self.chunk_size = get_appropriated_part_size(size) * 1024
        self.parts = ceil(size / self.chunk_size)
        self.progress = 0  # number of completed chunks
        self.progress_callback = progress_callback
        self.total_connections = self._connection_count_for(size)
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _connection_count_for(
        self,
        file_size: int,
        max_connections: int = 20,
        full_size: int = 100 * 1024 * 1024,
    ) -> int:
        """
        Scale the number of parallel connections linearly with file size,
        capping at `max_connections` for files >= `full_size`.
        """
        if file_size >= full_size:
            return max_connections
        return ceil((file_size / full_size) * max_connections)

    async def _run_progress_loop(self) -> None:
        """
        Periodically invoke the progress callback without blocking the event loop.

        Polls every 5 seconds to avoid Telegram's bot API rate limits, then
        sends a final 100 % notification once all chunks complete.
        """
        if self.progress_callback is None:
            return

        while self.progress < self.parts:
            await self.progress_callback(self.progress, self.parts)
            await asyncio.sleep(5)

        # Signal completion with equal current/total values.
        await self.progress_callback(self.size, self.size)

# ---------------------------------------------------------------------------
# DownloadManager
# ---------------------------------------------------------------------------

class DownloadManager(CommonManager):
    """Downloads a Telegram document to disk using multiple parallel connections."""

    def __init__(
        self,
        message: Message,
        client: TelegramClient,
        progress_callback: ProgressCallback = None,
        **kwargs,
    ) -> None:
        if not (message.document and message.file):
            raise RuntimeError(
                "No file attached to this message. Verify the message before constructing DownloadManager."
            )

        self.message = message
        self.file: File = message.file
        doc: Document = message.document

        super().__init__(client, doc.size, progress_callback, **kwargs)

        self.name = sanitize_filename(str(self.file.name) if self.file.name else "unnamed_file")
        self.loc = InputDocumentFileLocation(
            id=doc.id,
            access_hash=doc.access_hash,
            file_reference=doc.file_reference,
            thumb_size="",
        )
        self.connection_manager: ConnectionManager | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_chunk(
        self,
        sender: MTProtoSender,
        offset: int,
    ) -> bytes:
        """Request one chunk from Telegram and return the raw bytes."""
        result = await sender.send(  # type: ignore
            GetFileRequest(location=self.loc, offset=offset, limit=self.chunk_size)
        )
        return result.bytes

    async def _write_chunk(self, file_path: Path, offset: int, data: bytes) -> None:
        """Write `data` at `offset` inside an existing binary file."""
        async with aiofiles.open(file_path.resolve(), "r+b") as f:
            await f.seek(offset)
            await f.write(data)
            await f.flush()

    def _finalise_temp_file(self, temp_file: Path, destination: str) -> None:
        """
        Rename the temp file to its permanent name.

        If a file with the same name already exists in `destination`, a random
        suffix is injected before the extension to avoid collisions.
        """
        stem = Path(self.name)
        perm_file = temp_file.parent / self.name

        if self.name in listdir(destination):
            perm_file = temp_file.parent / f"{stem.stem}{crypt(10)}{stem.suffix}"

        try:
            replace(temp_file, perm_file.resolve())
        except OSError:
            pass

    async def _init_connections(self) -> None:
        """Set up the ConnectionManager pointing at the file's home DC."""
        dc_input, *_ = get_input_location(self.message.document)
        self.connection_manager = ConnectionManager(
            client=self.client,
            dc_id=dc_input.id,
        )
        self.total_connections = self._connection_count_for(self.size)
        await self.connection_manager.create_connections(self.total_connections)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def download_file(self, destination_folder: str) -> None:
        """Download the file into `destination_folder`."""
        temp_file = Path(
            destination_folder,
            f"{Path(self.name).stem}_{crypt(15)}.bin",
        ).resolve()

        self._logger.info("FILE: %s (%s MB)", self.name, f"{self.size / (1024 * 1024):.2f}")
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.touch(exist_ok=True)

        await self._init_connections()

        total_connections = self.total_connections
        sem = asyncio.Semaphore(total_connections)
        failed_offsets: list[int] = []
        tqdm_bar = tqdm(total=self.parts, desc="Downloading")
        async def download_chunk(offset: int, index: int, tqdm_bar) -> None:
            """Download one chunk and write it; record offset on failure."""
            async with sem:
                self.progress += 1
                sender = self.connection_manager.senders[index % total_connections]
                try:
                    data = await self._fetch_chunk(sender, offset)
                    await self._write_chunk(temp_file, offset, data)
                    tqdm_bar.update(1)
                except Exception:
                    failed_offsets.append(offset)

        # Initial pass: download all chunks in parallel.
        await asyncio.gather(
            *(
                download_chunk(
                    offset=i * self.chunk_size,
                    index=i,
                    tqdm_bar=tqdm_bar
                )
                for i in range(self.parts)
            ),
            self._run_progress_loop(),
        )

        # Retry loop: keep retrying until every chunk succeeds.
        while failed_offsets:
            pending = failed_offsets.copy()
            failed_offsets.clear()
            tqdm_bar = tqdm(total=len(pending), desc="Retry")
            await asyncio.gather(
                *(
                    download_chunk(offset=offset, index=idx, tqdm_bar=tqdm_bar)
                    for idx, offset in enumerate(pending)
                )
            )

        await self.connection_manager.disconnect_all()
        self._finalise_temp_file(temp_file, destination_folder)

# ---------------------------------------------------------------------------
# UploadManager
# ---------------------------------------------------------------------------

class UploadManager(CommonManager):
    """Uploads a local file to Telegram using multiple parallel connections."""

    # Threshold above which Telegram requires the "big file" upload API.
    BIG_FILE_THRESHOLD = 10 * 1024 * 1024  # 10 MB

    def __init__(
        self,
        client: TelegramClient,
        file_path: str | Path,
        progress_callback: ProgressCallback = None,
        **kwargs,
    ) -> None:
        self.file_path = Path(file_path)

        if not self.file_path.exists():
            raise FileNotFoundError(
                f"File not found: {self.file_path}. Validate the path before constructing UploadManager."
            )

        super().__init__(client, getsize(self.file_path), progress_callback, **kwargs)

        self.file_id: int = helpers.generate_random_long()
        self.connection_manager: ConnectionManager | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _init_connections(self) -> None:
        """Connect to the session's home DC for uploading."""
        dc = await self.client._get_dc(self.client.session.dc_id)  # type: ignore
        self.connection_manager = ConnectionManager(client=self.client, dc_id=dc.id)
        await self.connection_manager.create_connections(self.total_connections)

    async def _send_chunk(
        self,
        sender: MTProtoSender,
        part_index: int,
        data: bytes,
        is_big: bool,
    ) -> None:
        """Send one file part via the appropriate Telegram upload request."""
        if is_big:
            request = SaveBigFilePartRequest(
                file_id=self.file_id,
                file_part=part_index,
                file_total_parts=self.parts,
                bytes=data,
            )
        else:
            request = SaveFilePartRequest(
                file_id=self.file_id,
                file_part=part_index,
                bytes=data,
            )
        await sender.send(request=request)

    async def _iter_chunks(self):
        """Async generator that yields raw chunks from the file."""
        async with aiofiles.open(self.file_path, mode="rb") as f:
            while True:
                data = await f.read(self.chunk_size)
                if not data:
                    break
                yield data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def upload_file(self) -> InputFile | InputFileBig | None:
        """
        Upload the file and return a Telegram InputFile handle.

        Returns `InputFileBig` for files > 10 MB, `InputFile` otherwise.
        The MD5 checksum is only computed for small files (Telegram requirement).
        """
        is_big = self.size > self.BIG_FILE_THRESHOLD
        try:
            await self._init_connections()

            total_connections = self.total_connections
            sem = asyncio.Semaphore(total_connections)
            progress_bar = tqdm(total=self.parts, desc="Uploading")
            md5 = hashlib.md5()

            async def upload_chunk(part_index: int, data: bytes) -> None:
                async with sem:
                    self.progress += 1
                    sender = self.connection_manager.senders[part_index % total_connections]
                    try:
                        await self._send_chunk(sender, part_index, data, is_big)
                        progress_bar.update(1)
                    except Exception:
                        self._logger.warning("Chunk upload failed")
                        print(traceback.format_exc(limit=20))  # Failed parts are silently skipped (caller can validate).

            # Queue all chunk tasks while streaming the file sequentially.
            tasks: list[asyncio.Task] = []
            part_index = 0
            async for chunk in self._iter_chunks():
                if not is_big:
                    md5.update(chunk)
                tasks.append(asyncio.create_task(upload_chunk(part_index, chunk)))
                part_index += 1

            await asyncio.gather(*tasks, self._run_progress_loop())
            await self.connection_manager.disconnect_all()

            file_name = self.file_path.name
            if is_big:
                return InputFileBig(id=self.file_id, parts=self.parts, name=file_name)
            return InputFile(
                id=self.file_id,
                parts=self.parts,
                name=file_name,
                md5_checksum=md5.hexdigest(),
            )
        except Exception:
            print(traceback.format_exc())
            return None
