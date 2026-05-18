from tg.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message

@Command(name="start")
async def start(event: Message, client: TelegramClient):
    await event.respond("Hello, send me an anime name and I will look it up for you!") 
