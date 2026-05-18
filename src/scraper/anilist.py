import aiohttp
from pathvalidate import sanitize_filename
from .config import PROVIDER

class AnilistGenerator:
    def __init__(self, page: int, units: int) -> None:
        self.page = page
        self.units = units

    async def generate(self):
        query = """
          query($page: Int, $per: Int) {
            Page(page: $page, perPage: $per) {
              media(type: ANIME, sort: POPULARITY_DESC) {
                id
                title { english }
                episodes
              }
            }
          }
        """
        content_types = ["sub", "dub"]

        session = aiohttp.ClientSession()
        try:
            res = await session.post(
                "https://graphql.anilist.co",
                json={
                    "query": query,
                    "variables": {"page": self.page, "per": self.units},
                },
                headers={"Content-Type": "application/json"},
            )
            data = await res.json()
            raw_anime_list = data["data"]["Page"]["media"]
            anime_list = []
            for anime in raw_anime_list:
                total_episodes = anime["episodes"]
                if not total_episodes:
                    continue
                for i in range(1, int(total_episodes) + 1):
                    for content_type in content_types:
                        anime_list.append(
                            {
                                "name": f"{anime["id"]}_{sanitize_filename(anime["title"]["english"]).replace(" ", "_")}_episode_{i}_{content_type}",
                                "url": f"{PROVIDER}/ani/{anime["id"]}/{i}/{content_type}",
                            }
                        )

            await session.close()
            return anime_list
        except:
            return []
