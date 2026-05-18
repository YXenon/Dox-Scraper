import asyncio
import mimetypes
from pathlib import Path
from tg.bot import client
from tg.constants import STORE_CHANNEL_ID
from tg.utils.parallel import UploadManager
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename

async def upload_to_telegram(ctx):

    ctx.app_ctx.wait_for("p2")
    metadata = ctx.internal_q.get()

    video_path = Path(metadata["video"])
    manager = UploadManager(client=client, file_path=video_path)
    file = await manager.upload_file()
    await client(SendMediaRequest(
            peer=STORE_CHANNEL_ID, # type: ignore
            media=InputMediaUploadedDocument(
                file=file,
                mime_type=(mimetypes.guess_type(video_path)[0] or "application/octet-stream"),
                attributes=[DocumentAttributeFilename(video_path.name)]
            ),
            message=video_path.name
        )
    )
    ctx.app_ctx.ok_q.put({"job": "upload", "status": "done"}, timeout=10)


def upload_job(ctx):
    asyncio.run(upload_to_telegram(ctx))