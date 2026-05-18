from tg.bot import client
from telethon import TelegramClient, events
# from telethon.events import Album
from telethon.tl.custom.message import Message
from pathlib import Path
from typing import Callable, Awaitable, Any
from importlib import import_module
FunctionType = Callable[[Message, TelegramClient], Awaitable[Any]]

def Command(name: str, allowed: list[int] | None = None):
    def decorator(func: FunctionType) -> FunctionType:
        @client.on(events.NewMessage(pattern=rf"/{name}(?:\s+(.+))?"))
        async def run(event: Message):
            if allowed is not None and len(allowed) > 0:
                if event.sender_id in allowed:
                    await func(event, client)
                else:
                    await event.respond("❌ Sorry, you aren't permitted to use this command.")
            else:
                await func(event, client)
        return func
    return decorator

def loadCommands(mainDir: Path):
    path = Path(mainDir).resolve() / "commands" # initialized from main.py file
    cmds = list(filter(lambda x: x.name != "__init__.py" and (not x.name.startswith("h_")) and x.suffix == ".py" and x.is_file(), path.glob("*.py")))
    for cmd in cmds:
        import_module(f"tg.commands.{cmd.stem}")
    print("=> Commands initialized on Telegram")