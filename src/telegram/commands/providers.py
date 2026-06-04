from constants import DEV_ID, SCRAPE_PROVIDERS
from telegram.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message

@Command(name="providers", allowed=[DEV_ID])
async def providers(event: Message, client: TelegramClient):
    response = "Providers:\n"
    for provider in SCRAPE_PROVIDERS:
        response += f"{provider.capitalize()}: `{provider}`\n"
    await event.respond(response)