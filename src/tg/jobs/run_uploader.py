import asyncio
import mimetypes
from pathlib import Path
import shutil
from tg.bot import client
from tg.constants import STORE_CHANNEL_ID
from tg.utils.parallel import UploadManager
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename, DocumentAttributeVideo

async def upload_to_telegram(ctx):

    await asyncio.to_thread(ctx.wait_for, "p2")
    while True:
        metadata = await asyncio.to_thread(ctx.data_q.get)

        video_path = Path(metadata["video"])
        manager = UploadManager(client=client, file_path=video_path)
        file = await manager.upload_file()
        await client(SendMediaRequest(
                peer=STORE_CHANNEL_ID, # type: ignore
                media=InputMediaUploadedDocument(
                    file=file,
                    mime_type=(mimetypes.guess_type(video_path)[0] or "application/octet-stream"),
                    attributes=[DocumentAttributeFilename(video_path.name), DocumentAttributeVideo(duration=0, w=0, h=0, supports_streaming=True)]
                ),
                message=video_path.name
            )
        )
        ctx.ok_q.put({"job": "upload", "status": "done"}, timeout=10)

        # Delete the folder once work is done
        shutil.rmtree(video_path.parent)

def upload_job(ctx):
    asyncio.run(upload_to_telegram(ctx))