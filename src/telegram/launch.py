import asyncio

from telegram.jobs.run_bot import run_bot
from telegram.jobs.run_uploader import upload_to_telegram

async def telegram_jobs():
    await asyncio.gather(
        run_bot(),
        upload_to_telegram()
    )