from handlers.threads import ThreadContext
from handlers.process import app_ctx
from tg.jobs.run_bot import bot_job
from tg.jobs.run_uploader import upload_job

async def telegram_jobs():
    threads = ThreadContext(app_ctx)
    threads.register("t1", bot_job)
    threads.register("t2", upload_job)
    threads.start("t1")
    threads.start("t2")
    threads.join_all()