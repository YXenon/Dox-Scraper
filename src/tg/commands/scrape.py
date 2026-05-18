from tg.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message
from handlers.process import app_ctx

@Command(name="scrape", allowed=[1314824862])
async def scrape(event: Message, client: TelegramClient):
    # app_ctx.start("p1")
    await event.respond("Not startable yet!")
