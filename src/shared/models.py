from typing import Literal, Union

from beanie import Document
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Inter-process models
# ---------------------------------------------------------------------------

class Metadata(BaseModel):
    """
    File transfer metadata shared between scraper, converter, and uploader processes.

    Always serialize via ``model_dump_json()`` and deserialize via
    ``model_validate_json()`` before passing across process boundaries.
    """

    video:     str       = ""
    parts:     int       = 0
    subtitles: list[str] = Field(default_factory=list)  # avoid shared mutable default
    dir:       str       = ""


class AutoMode(BaseModel):
    """Trigger the automatic bulk-scraping pipeline."""
    mode: Literal["auto"]


class RequestedMode(BaseModel):
    """Trigger a single user-requested episode scrape."""
    mode:         str
    provider:     str
    url:          str
    anilist_id:   int
    episode:      int | Literal[""] = ""
    content_type: str | Literal[""] = ""


# Discriminated union of all supported scraping request types.
ScrapingRequests = Union[AutoMode, RequestedMode]

# ---------------------------------------------------------------------------
# MongoDB document models
# ---------------------------------------------------------------------------

class TelegramFile(Document):
    """
    Persists a reference to an uploaded Telegram file for later retrieval.

    ``queries`` holds tokenized search terms derived from the filename stem,
    used to look up the file without storing the full path.
    """
    channel_id: int
    msg_id:     int
    queries:    list[str]
    anilist_id: int

    class Settings:
        name = "telegram_files"