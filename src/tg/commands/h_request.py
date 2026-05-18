from pathlib import Path
import mimetypes
from tg.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename
from tg.utils.parallel import UploadManager

from math import ceil
async def callback(n: int, d: int):
    print(ceil((n/d)*100), "%")

@Command(name="find")
async def find(event: Message, client: TelegramClient):
    file_path = Path("src/__pycache__/bot.cpython-313.pyc")
    print(file_path.exists())
    input_file = await UploadManager(client, file_path, callback).upload_file()
    await client(SendMediaRequest(
            peer=event.chat_id,
            media=InputMediaUploadedDocument(
                file=input_file,
                mime_type=(mimetypes.guess_type(file_path)[0] or "application/octet-stream"),
                attributes=[DocumentAttributeFilename(file_path.name)]
            ),
            message=file_path.name
        )
    )