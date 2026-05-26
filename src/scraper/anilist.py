import traceback

import aiohttp


# GraphQL query to fetch anime with episode counts from AniList
ANILIST_GENERATOR_QUERY = """
    query ($page: Int, $perPage: Int) {
        Page(page: $page, perPage: $perPage) {
            media(type: ANIME) {
                episodes
                id
                idMal
                type
                format
                status
                description
                season
                seasonYear
                seasonInt
                episodes
                duration
                countryOfOrigin
                source
                hashtag
                updatedAt
                bannerImage
                synonyms
                siteUrl
                title {
                    romaji
                    english
                    native
                    userPreferred
                }
            }
        }
    }
"""

ANILIST_FIND_QUERY = """
    query($id: Int) {
        Media(id: $id, type: ANIME) {
                id
                idMal
                type
                format
                status
                description
                season
                seasonYear
                seasonInt
                episodes
                duration
                countryOfOrigin
                source
                hashtag
                updatedAt
                bannerImage
                synonyms
                siteUrl
                title {
                    romaji
                    english
                    native
                    userPreferred
                }
        }
    }
"""

ANILIST_API_URL = "https://graphql.anilist.co"


class Anilist:
    """Generates a list of anime episode entries from AniList's most popular titles."""

    def __init__(self) -> None:
        pass

    async def generate(self, page: int, units: int) -> list[dict]:
        """
        Fetches anime from AniList and returns a flat list of episode entries,
        each with a sanitized name and provider URL for both sub and dub.
        Returns an empty list on failure.
        """
        variables = {"page": page, "perPage": units}

        async with aiohttp.ClientSession() as session:
            try:
                response = await session.post(
                    ANILIST_API_URL,
                    json={"query": ANILIST_GENERATOR_QUERY, "variables": variables},
                    headers={"Content-Type": "application/json"},
                )
                data = await response.json()
                anime_list = data["data"]["Page"]["media"]

                return anime_list

            except Exception:
                return []
    
    async def find(self, ani_id: int):
        
        async with aiohttp.ClientSession() as session:
            try:
                response = await session.post(
                    ANILIST_API_URL,
                    json={"query": ANILIST_FIND_QUERY, "variables": {"id": ani_id}},
                    headers={"Content-Type": "application/json"},
                )
                data = await response.json()
                if data:
                    anime_list = data["data"]["Media"]
                    return anime_list
                
                return None

            except Exception:
                print(traceback.format_exc())
                return None