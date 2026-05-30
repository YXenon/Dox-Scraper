from typing import Literal, Union

from beanie import Document
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Inter-process models
# ---------------------------------------------------------------------------

class Metadata(BaseModel):
    video:     str       = ""
    parts:     int       = 0
    subtitles: list[str] = Field(default_factory=list)
    dir:       str       = ""


class AutoMode(BaseModel):
    mode: Literal["auto"]


class RequestedMode(BaseModel):
    """Single episode scrape. url is optional — built automatically if omitted."""
    mode:         Literal["request"]
    provider:     str
    anilist_id:   int
    episode:      int
    content_type: str = "sub"
    url:          str = ""          # if empty, URLBuilder constructs it


class AnimeMode(BaseModel):
    """Scrape all available episodes for one anime."""
    mode:         Literal["anime"]
    provider:     str
    anilist_id:   int


ScrapingRequests = Union[AutoMode, RequestedMode, AnimeMode]

# ---------------------------------------------------------------------------
# MongoDB document models
# ---------------------------------------------------------------------------

class TelegramFile(Document):
    channel_id: int
    msg_id:     int
    queries:    list[str]
    anilist_id: int

    class Settings:
        name = "telegram_files"


class ScrapedAnime(Document):
    anilist_id:      int
    title:           str
    is_airing:       bool       = False
    total_episodes:  int | None = None
    sub:             list[int]  = Field(default_factory=list)
    dub:             list[int]  = Field(default_factory=list)
    failed_sub: list[int] = Field(default_factory=list)
    failed_dub: list[int] = Field(default_factory=list)

    class Settings:
        name = "scraped_anime"

    def scraped(self, content_type: str) -> list[int]:
        return self.sub if content_type == "sub" else self.dub
    
    def failed(self, content_type: str) -> list[int]:
        return self.failed_sub if content_type == "sub" else self.failed_dub

    def missing_episodes(self, content_type: str) -> list[int]:
        if self.total_episodes is None:
            return []
        have = set(self.scraped(content_type))
        return [ep for ep in range(1, self.total_episodes + 1) if ep not in have]

    def is_complete(self, content_type: str) -> bool:
        if self.total_episodes is None:
            return False
        return len(self.scraped(content_type)) >= self.total_episodes

    async def mark_scraped(self, episode: int, content_type: str) -> None:
        episodes = self.scraped(content_type)
        if episode not in episodes:
            episodes.append(episode)
            episodes.sort()
        # clear from failed if it was retried successfully
        if episode in self.failed(content_type):
            self.failed(content_type).remove(episode)
        await self.save()

    async def mark_failed(self, episode: int, content_type: str) -> None:
        failed = self.failed(content_type)
        if episode not in failed:
            failed.append(episode)
            failed.sort()
        await self.save()

    @classmethod
    def _resolve_total_episodes(cls, anime_info: dict) -> int | None:
        is_airing = anime_info.get("status") == "RELEASING"

        if is_airing:
            next_airing = anime_info.get("nextAiringEpisode")
            if next_airing and next_airing.get("episode"):
                return int(next_airing["episode"]) - 1
            return None  # airing but no nextAiringEpisode key → skip

        # Finished airing — episodes must exist
        if anime_info.get("episodes"):
            return int(anime_info["episodes"])
        return None

    @classmethod
    async def get_or_create(
        cls,
        anilist_id: int,
        title: str,
        is_airing: bool,
        anime_info: dict,               # pass full info so we can resolve here
    ) -> "ScrapedAnime | None":         # None = couldn't resolve episode count
        total_episodes = cls._resolve_total_episodes(anime_info)
        if total_episodes is None:
            return None

        doc = await cls.find_one(cls.anilist_id == anilist_id)
        if doc:
            # Update if: finished airing and count is now known, or Anilist has a higher count
            new_total = total_episodes
            if new_total is not None and (
                doc.total_episodes is None or new_total > doc.total_episodes
            ):
                doc.total_episodes = new_total
                doc.is_airing = is_airing
                await doc.save()
            return doc

        doc = cls(
            anilist_id=anilist_id,
            title=title,
            is_airing=is_airing,
            total_episodes=total_episodes,
        )
        await doc.insert()
        return doc
