from core.handlers.process import app_ctx
from core.jobs.bot import launch_bot_job
from core.jobs.scrape import launch_scrape_job

def main():
    app_ctx.register("p1", launch_bot_job)
    app_ctx.register("p2", launch_scrape_job)
    app_ctx.start("p2") # order matters, uploader may hang if p1 is started first
    app_ctx.start("p1")

    app_ctx.process_commands()

if __name__ == "__main__":
    main()
