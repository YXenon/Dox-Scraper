from typing import Any

from constants import SCRAPE_PROVIDERS, DEV_ID
from shared.models import AutoMode, RequestedMode, AnimeMode
from telegram.handlers.commands import Command
from telethon import TelegramClient
from telethon.tl.custom.message import Message
from core.handlers.process import app_ctx

help_str = """
Invalid parameters.

**Modes:**
`/scrape auto`
`/scrape [provider] [anilist_id]` — scrape all episodes for an anime
`/scrape [provider] [anilist_id] [episode] [content_type]` — single episode (url optional)
`/scrape [provider] [url] [anilist_id] [episode] [content_type]` — single episode with explicit url

**Parameters:**
`[provider]`: Any of the registered providers, use `/providers` to list all of them.

`[url]`: Url to scrape. Must be same as the provider's registered url. Will be ignored otherwise.

`[anilist_id]`: Can be found at `https://anilist.co/search/anime`. If wrong, request is ignored.

`[episode]`: Episode no to scrape, request will be ignored if episode specified is greater than total episodes.

`[content_type]`: "sub" or "dub" (without quotes).If not specified, uses "sub".

**Examples:**
`/scrape auto`
`/scrape anikoto 21`
`/scrape anikoto 21 5 sub`
`/scrape anikoto https://megaplay.buzz/stream/ani/21/5/sub 21 5 sub`

"""

success_str = "Added to queue. \n\nDetails:\n"

MAX_PARAMS = 5


def _is_int(s: Any) -> bool:
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_url(s: str) -> bool:
    return s.startswith("https://")


@Command(name="scrape", allowed=[DEV_ID])
async def scrape(event: Message, client: TelegramClient):
    group: str = event.pattern_match.group(1)  # type: ignore
    if not group:
        await event.respond(help_str)
        return

    params = group.split()
    n = len(params)

    # /scrape auto
    if params[0] == "auto":
        app_ctx.scrape_q.put(AutoMode(mode="auto"))
        await event.respond(success_str + "**Mode:** Auto")
        return

    provider = params[0]

    if provider not in SCRAPE_PROVIDERS:
        await event.respond(help_str)
        return

    # Detect whether second param is a URL or an anilist id
    has_url = n >= 2 and _is_url(params[1])

    if has_url:
        # /scrape anikoto <url> <anilist_id> <episode> <content_type>
        if n < 5 or not _is_int(params[2]) or not _is_int(params[3]) or params[4] not in ("sub", "dub"):
            await event.respond(help_str)
            return
        url, anilist_id, episode, content_type = params[1], int(params[2]), int(params[3]), params[4]
        data = RequestedMode(
            mode="request", provider=provider,
            anilist_id=anilist_id, episode=episode,
            content_type=content_type, url=url,
        )
        app_ctx.scrape_q.put(data)
        await event.respond(
            success_str +
            f"**Provider:** {provider}\n**URL:** `{url}`\n"
            f"**Anilist id:** {anilist_id}\n**Episode:** {episode}\n**Type:** {content_type}"
        )

    elif n == 2 and _is_int(params[1]):
        # /scrape anikoto 21  →  full anime scrape
        anilist_id = int(params[1])
        data = AnimeMode(mode="anime", provider=provider, anilist_id=anilist_id)
        app_ctx.scrape_q.put(data)
        await event.respond(success_str + f"**Mode:** Full anime\n**Provider:** {provider}\n**Anilist id:** {anilist_id}")

    elif n >= 3 and _is_int(params[1]) and _is_int(params[2]):
        # /scrape anikoto 21 5 [sub|dub]
        anilist_id, episode = int(params[1]), int(params[2])
        content_type = params[3] if n >= 4 and params[3] in ("sub", "dub") else "sub"
        data = RequestedMode(
            mode="request", provider=provider,
            anilist_id=anilist_id, episode=episode,
            content_type=content_type,
        )
        app_ctx.scrape_q.put(data)
        await event.respond(
            success_str +
            f"**Provider:** {provider}\n**Anilist id:** {anilist_id}\n"
            f"**Episode:** {episode}\n**Type:** {content_type}"
        )

    else:
        await event.respond(help_str)