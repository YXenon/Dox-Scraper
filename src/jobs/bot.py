import asyncio
from tg.launch import telegram_jobs

def launch_bot_job(*_):
    print("=> Starting bot")
    asyncio.run(telegram_jobs())