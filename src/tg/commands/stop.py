from tg.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message
from handlers.process import app_ctx

@Command(name="stop", allowed=[1314824862])
async def scrape(event: Message, client: TelegramClient):
    s2 = app_ctx.request_stop("p2")
    if s2:
        await event.respond("✅ Stopped scraper!")
    else:
        await event.respond("❌ Couldn't stop scraper!")