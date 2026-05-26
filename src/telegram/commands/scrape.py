from typing import Any

from constants import SCRAPE_PROVIDERS
from shared.models import AutoMode, RequestedMode
from telegram.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message
from core.handlers.process import app_ctx

help_str = """
Invalid parameters. Please follow the following pattern:
`/scrape [mode] [provider] [url] [anilist id] [episode] [content type]`

**Mode:** You can pass "auto" (without quotes) and not specify other parameters, as it defaults to scraping automatically.

**Required:**
`[provider]` → registered provider name
`[url]` → url for scraping
`[anilist id]` → get the anime id from `https://anilist.co/search/anime`

**Optional:**
`[episode]` → episode no (optional) 
`[content type]` → "sub" or "dub" (without quotes)

**Valid Usage:**
`/scrape auto`
`/scrape anikoto https://megaplay.buzz/stream/ani/1/23/sub 1 23 sub`
`/scrape anikoto https://megaplay.buzz/stream/ani/178788 178788`
"""

success_str = "Your request will be processed as soon as the previous request is completed.\n\nYour request details:\n"

MAX_PARAMS = 5


def is_intable(s: Any):
    """Check if is convertible to int"""
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


@Command(name="scrape", allowed=[1314824862])
async def scrape(event: Message, client: TelegramClient):
    group: str = event.pattern_match.group(1)  # type: ignore
    if group:
        params = group.split()
        total_params = len(params)
        if total_params < 1 or total_params > MAX_PARAMS:
            await event.respond(help_str)
            return

        params += ["" for _ in range(MAX_PARAMS - total_params)]
        provider, url, anilist_id, episode, content_type = params[:MAX_PARAMS]

        if provider=="auto":
            data = AutoMode(mode="auto")
            app_ctx.scrape_q.put(data)
            await event.respond(success_str+"**Mode:** Auto")

        else:
            valid = (
                provider in SCRAPE_PROVIDERS,
                url.startswith("https://"),
                is_intable(anilist_id),
                is_intable(episode),
                content_type in ["sub", "dub"],
            )

            if all(valid):
                data = RequestedMode(
                    mode="request",
                    provider=provider,
                    url=url,
                    anilist_id=int(anilist_id),
                    episode=int(episode),
                    content_type=content_type,
                )

                app_ctx.scrape_q.put(data)
                await event.respond(success_str+f"**Provider:** {provider}\n**Url:** `{url}`\n**Anilist id:** {anilist_id}\n**Episode no:** {episode}\n**Content type:** {content_type}")

    else:
        await event.respond(help_str)
