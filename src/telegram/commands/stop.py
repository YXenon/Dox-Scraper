from telegram.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message
from core.handlers.process import app_ctx
from constants import DEV_ID

@Command(name="stop", allowed=[DEV_ID])
async def scrape(event: Message, client: TelegramClient):
    app_ctx.request_stop("p2")
    await event.respond("Stopped scraper!")