import importlib
from pathlib import Path
import shutil
import traceback

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

TEMP_DIR = Path("./temp")

def _clear_temp() -> None:
    """Remove the temporary working directory, ignoring it if it doesn't exist."""
    try:
        shutil.rmtree(TEMP_DIR)
    except FileNotFoundError:
        print(traceback.format_exc())


def _load_provider(provider: str):
    """
    Dynamically import a scraping provider module and return its
    URLBuilder and Scraper classes.
    """
    module = importlib.import_module(f".providers.{provider}", package=__package__)
    return module.URLBuilder, module.Scraper
