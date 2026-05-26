import logging

from telegram.bot import client
from telethon import TelegramClient, events
from telethon.tl.custom.message import Message
from pathlib import Path
from typing import Callable, Awaitable, Any
from importlib import import_module
FunctionType = Callable[[Message, TelegramClient], Awaitable[Any]]

def Command(name: str, allowed: list[int] | None = None):
    def decorator(func: FunctionType) -> FunctionType:
        @client.on(events.NewMessage(pattern=rf"/{name}(?:\s+(.+))?"))
        async def run(event: Message):
            if allowed is not None and len(allowed):
                if event.sender_id in allowed:
                    await func(event, client)
                else:
                    await event.respond("❌ Sorry, you aren't permitted to use this command.")
            else:
                await func(event, client)
        return func
    return decorator

def loadCommands(mainDir: Path):
    logger = logging.getLogger(__name__)
    path = Path(mainDir).resolve() / "commands" # mainDir should point to the parent dir of 'commands'
    cmds = list(
                filter(
                    lambda x:
                    x.name != "__init__.py"
                    and (not x.name.startswith("h_")),
                    path.glob("*.py")
                )
            )
    
    for cmd in cmds:
        import_module(f"telegram.commands.{cmd.stem}")
    logger.info("Commands initialized on Telegram")
    