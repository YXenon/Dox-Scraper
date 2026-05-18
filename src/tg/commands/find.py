from tg.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message

@Command(name="find")
async def find(event: Message, client: TelegramClient):
    group: str = event.pattern_match.group(1) # type: ignore
    if group:
        args = group.split(" ")
        await event.respond(f"Your arguments: {args}")
    else:
        await event.respond("No arguments passed.\nExample usage: `/find Bleach ep 120`")