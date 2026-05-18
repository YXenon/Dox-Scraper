# This script takes inspiration from Tulir Asokan's mautrix repo. It is cleaned and some useful features are
# added on top of it like, better chunk handling, progress callback that doesn't ratelimit your bot.
import asyncio
import aiofiles
import hashlib
from pathvalidate import sanitize_filename
from pathlib import Path
from telethon import TelegramClient, helpers
from telethon.network import MTProtoSender
from telethon.tl.custom.message import Message
from telethon.tl.types import (InputDocumentFileLocation, Document, InputFile, InputFileBig)
from telethon.tl.functions.upload import (GetFileRequest, SaveFilePartRequest,
                                          SaveBigFilePartRequest)
from telethon.utils import get_appropriated_part_size, get_input_location
from telethon.tl.functions.auth import (
    ExportAuthorizationRequest,
    ImportAuthorizationRequest,
)
from telethon.tl.custom.file import File
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.alltlobjects import LAYER
from os import replace, listdir
from os.path import getsize

from tqdm.asyncio import tqdm
from .crypto_str import crypt
from math import ceil
from typing import Callable, Any, Awaitable

class ConnectionManager:
    def __init__(self, client: TelegramClient, dc: int) -> None:
        self.client = client
        self.dc_id = dc.id
        self.senders: list[MTProtoSender] = []

    async def init(self):
        self.dc = await self.client._get_dc(self.dc_id)
        self.auth_key = self.client.session.auth_key if self.client.session.dc_id == self.dc_id else None  # type: ignore


    async def _create_connection(self) -> MTProtoSender:
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
        if not self.auth_key:
            auth = await client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(id=auth.id, bytes=auth.bytes) # type: ignore
            await sender.send(InvokeWithLayerRequest(LAYER, client._init_request))  # type: ignore
            self.auth_key = sender.auth_key
        self.senders.append(sender)
        return sender

    async def create_connections(self, connections: int) -> None:
        if not getattr(self, "dc", None): await self.init()
        await self._create_connection() # The first cross-DC sender will export+import the authorization.
        await asyncio.gather(
            *(self._create_connection() for _ in range(connections - 1))
        )

    async def disconnect_connections(self) -> None:
        if len(self.senders)==0: return
        [await sender.disconnect() for sender in self.senders]


class CommonManager:
    client: TelegramClient
    size: int
    chunk_size: int
    parts: int
    total_connections: int
    progress: int
    progress_callback: Callable[[int, int], Awaitable[Any]] | None
    def __init__(self, client: TelegramClient, size: int, progress_callback: Callable[[int, int], Awaitable[Any]] | None = None) -> None:
        self.client = client
        self.size = size
        self.chunk_size = get_appropriated_part_size(self.size) * 1024 # get_appropriated_part_size returns size in kB
        self.parts = ceil(self.size / self.chunk_size)
        self.progress = 0
        self.progress_callback = progress_callback
        self.total_connections = self._get_connection_count(self.size)

    def _get_connection_count(
        self, file_size: int, full_size: int = 100 * 1024 * 1024
    ) -> int:
        max_connections = 20
        if file_size > full_size:
            return max_connections
        return ceil((file_size / full_size) * max_connections)
    
    async def _progress_callback(self):
        callback = self.progress_callback
        if callback is None:
            return
        while self.progress < self.parts:
            await callback(self.progress, self.parts)
            await asyncio.sleep(5)

        await callback(self.size, self.size)

class DownloadManager(CommonManager):
    message: Message
    connection_manager: ConnectionManager
    file: File
    document: Document
    name: str
    loc: InputDocumentFileLocation
    def __init__(self, message: Message, client: TelegramClient, progress_callback: Callable[[int, int], Awaitable[Any]] | None = None, **kwargs) -> None:
        if not (message.document and message.file):
            raise RuntimeError("No file in the message. Please check before creating an instance.")

        self.message = message
        self.file = message.file
        doc = message.document
        super().__init__(client, doc.size, progress_callback, **kwargs)
        self.name = sanitize_filename(str(self.file.name) if self.file else "unnamed_File")
        self.loc = InputDocumentFileLocation(
            id=doc.id,
            access_hash=doc.access_hash,
            file_reference=doc.file_reference,
            thumb_size="",
        )

    async def _download_chunk(
        self,
        sender: MTProtoSender,
        loc: InputDocumentFileLocation,
        offset: int,
        limit: int,
    ) -> bytes:
        result = await sender.send(request=GetFileRequest(location=loc, offset=offset, limit=limit)) # type: ignore
        return result.bytes

    async def _write_to_file(
        self,
        file: Path,
        offset: int,
        data: bytes
    ):
        async with aiofiles.open(file.resolve(), "r+b") as f:
            await f.seek(offset)
            await f.write(data)
            await f.flush()

    def _resolve_temp_file(self, temp_file: Path, dst: str):
        perm_file = Path(temp_file.parent / self.name).resolve()
        if self.name in listdir(dst):
            file = Path(self.name)
            perm_file = Path(temp_file.parent / f"{file.stem}{crypt(10)}{file.suffix}")
        try: replace(temp_file, perm_file)
        except: pass
    
    async def _init_connection_manager(self):
        self.connection_manager = ConnectionManager(
            client=self.client,
            dc=get_input_location(self.message.document)[0]
        )
        self.total_connections = self._get_connection_count(self.size)
        print("📡 TOTAL CONNECTIONS:", self.total_connections)
        await self.connection_manager.create_connections(self.total_connections)

    async def download_file(self, destination_folder: str):
        '''Call download execution.'''

        temp_file = Path(destination_folder, f"{Path(self.name).stem}_{crypt(15)}.bin").resolve()
        print("\n📁 FILE:", self.name, f"({self.size/(1024*1024):.2f}MB)")
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.touch(exist_ok=True)

        await self._init_connection_manager()
        total_connections = self.total_connections

        sem = asyncio.Semaphore(total_connections)
        failed_chunks: list[int] = []
        async def download_and_write(offset: int, multiplier: int = 0, parts: int = self.parts):
            async with sem:
                try:
                    self.progress += 1
                    data = await self._download_chunk(
                        sender=self.connection_manager.senders[multiplier % total_connections],
                        loc=self.loc,
                        offset=offset,
                        limit=self.chunk_size,
                    )
                    await self._write_to_file(file=temp_file, offset=offset, data=data)
                    print(f"📝 CHUNK {multiplier+1}/{parts} WRITTEN!")
                except:
                    failed_chunks.append(offset)

        await asyncio.gather(*(
            *(
                download_and_write(offset=i*self.chunk_size, multiplier=i)
                for i in range(self.parts)
            ),
            self._progress_callback()
            ))
        
        while True:
            failed = failed_chunks.copy()
            total_failed = len(failed)
            if total_failed==0:
                break
            else:
                failed_chunks.clear()
                await asyncio.gather(
                    *(download_and_write(
                        offset=item, multiplier=index, parts=total_failed)
                        for index, item in enumerate(failed)
                    ))


        await self.connection_manager.disconnect_connections()
        self._resolve_temp_file(temp_file, destination_folder)

        print("✅ COMPLETED!")

class UploadManager(CommonManager):
    file_path: Path
    file_id: int
    connection_manager: ConnectionManager

    def __init__(self, client: TelegramClient, file_path: str | Path, progress_callback: Callable[[int, int], Awaitable[Any]] | None = None, **kwargs) -> None:

        self.file_path = Path(file_path)

        if not self.file_path.exists():
            raise FileExistsError("File does not exist, please validate the path before passing.")

        super().__init__(client, getsize(self.file_path), progress_callback, **kwargs)
        self.file_id = helpers.generate_random_long()

    async def _init_connection_manager(self):
        dc = await self.client._get_dc(self.client.session.dc_id) # type: ignore
        self.connection_manager = ConnectionManager(client=self.client, dc=dc)
        await self.connection_manager.create_connections(self.total_connections)
    
    async def _upload_chunk(self, sender: MTProtoSender, file_id: int, big: bool, current: int, parts: int, data: bytes):
        if big:
            # Big file size (>10 MB)
            request=SaveBigFilePartRequest(
                    file_id=file_id,
                    file_part=current,
                    file_total_parts=parts,
                    bytes=data
                )
        else:
            request=SaveFilePartRequest(
                    file_id=file_id,
                    file_part=current,
                    bytes=data
                )
        await sender.send(request=request)

    async def _stream(self):
        async with aiofiles.open(self.file_path, mode="rb") as f:
            while True:
                data = await f.read(self.chunk_size)
                if not data:
                    break
                yield data

    async def upload_file(self):
        '''Call upload execution.'''
        big = self.size > (10 * 1024 * 1024)
        await self._init_connection_manager()
        total_connections = self.total_connections
        sem = asyncio.Semaphore(total_connections)
        tqdm_bar = tqdm(total=self.parts, desc="Uploading ")
        async def upload(multiplier: int, data: bytes):
            async with sem:
                self.progress+=1
                try:
                    await self._upload_chunk(
                        sender=self.connection_manager.senders[multiplier % total_connections],
                        file_id=self.file_id,
                        big=big,
                        current=multiplier,
                        parts=self.parts,
                        data=data
                    )
                    tqdm_bar.update(1)
                except:
                    pass

        multiplier = 0
        tasks = []
        md5 = hashlib.md5()
        async for data in self._stream():
            if not big:
                md5.update(data)
            task = asyncio.create_task(upload(multiplier, data))
            tasks.append(task)
            multiplier+=1
        
        await asyncio.gather(*(*tasks, self._progress_callback()))
     

        await self.connection_manager.disconnect_connections()

        file_name = self.file_path.name
        if big:
            return InputFileBig(id=self.file_id, parts=self.parts, name=file_name)
        return InputFile(id=self.file_id, parts=self.parts, name=file_name, md5_checksum=md5.hexdigest())
