import asyncio
from pathlib import Path
from ..bot import client
from ..constants import BOT_TOKEN
from ..handlers.commands import loadCommands

async def run_bot():
    await client.start(bot_token=BOT_TOKEN) # type: ignore
    loadCommands(Path(__file__).parent.parent) # be careful of this, it should point to 'commands' dir's parent dir
    if bot:= await client.get_me():
        name = bot.first_name if bot.first_name else "BOT" if bot.bot else "USER" # type: ignore
        print(f"=> Logged in as {name} on Telegram")
    await client.run_until_disconnected() # type: ignore

def bot_job(*_):
    asyncio.run(run_bot())
