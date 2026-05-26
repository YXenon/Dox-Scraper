import logging
from pathlib import Path

from ..bot import client
from constants import BOT_TOKEN
from ..handlers.commands import loadCommands

# The commands directory lives one level above this file's parent package.
_COMMANDS_ROOT = Path(__file__).parent.parent


async def run_bot() -> None:
    """
    Start the Telegram bot and block until it disconnects.

    Steps
    -----
    1. Authenticate with the Bot API using BOT_TOKEN.
    2. Discover and register command handlers under the commands directory.
    3. Log the account name to confirm a successful login.
    4. Enter the Telethon event loop (returns only on disconnection).
    """
    logger = logging.getLogger(__name__)
    await client.start(bot_token=BOT_TOKEN)  # type: ignore[union-attr]

    # loadCommands expects the *parent* of the 'commands' directory.
    loadCommands(_COMMANDS_ROOT)


    identity = await client.get_me()  # type: ignore[union-attr]
    if identity:
        if identity.first_name:
            display_name = identity.first_name
        elif identity.bot:  # type: ignore[union-attr]
            display_name = "BOT"
        else:
            display_name = "USER"

        logger.info("Logged in as %s on Telegram", display_name)

    await client.run_until_disconnected()  # type: ignore[union-attr]