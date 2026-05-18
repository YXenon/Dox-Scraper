import asyncio

from handlers.process import app_ctx
from tg.jobs.run_bot import run_bot
from tg.jobs.run_uploader import upload_to_telegram

async def telegram_jobs():
    await asyncio.gather(
        run_bot(),
        upload_to_telegram(app_ctx)
    )