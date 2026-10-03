"""
TMDB Provider - The Movie Database API integration
Uses TMDB API v3 to search and fetch movie metadata.
"""

import logging
from typing import List, Optional

from core.models import Actor, Metadata, ParsedFilename, SearchResult, VideoType
from providers.base import BaseProvider
from providers.registry import register_provider

logger = logging.getLogger(__name__)

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"


@register_provider("tmdb")
class TMDBProvider(BaseProvider):
    """TMDB (The Movie Database) Provider"""

    def __init__(self, api_key: str = "", proxy: str = "", **kwargs):
        self._api_key = api_key
        super().__init__(proxy=proxy, connector=kwargs.get("connector"), **kwargs)
        # Read TMDB-specific config from kwargs
        self._language = kwargs.get("language", "zh-CN")
        self._region = kwargs.get("region", "CN")
        if not self._api_key:
            logger.warning("[tmdb] API Key not configured, provider will not work")

    @property
    def name(self) -> str:
        return "tmdb"

    @property
    def display_name(self) -> str:
        return "TMDB (The Movie Database)"

    @property
    def supported_types(self) -> List[str]:
        # TMDB is for general movies, not specialized coded content
        return ["movie", "episode"]

    def can_handle(self, parsed: ParsedFilename) -> bool:
        """TMDB only handles movie and episode types, skip coded (番号) files."""
        vtype = parsed.video_type.value
        # Skip coded type - TMDB won't have Japanese AV metadata
        if vtype == "coded":
            return False
        return vtype in self.supported_types

    async def search(self, query: str, parsed: ParsedFilename) -> List[SearchResult]:
        """Search TMDB for movies."""
        if not self._api_key:
            return []

        url = f"{TMDB_API_BASE}/search/movie"
        params = {
            "api_key": self._api_key,
            "query": query,
            "language": self._language,
            "region": self._region,
            "include_adult": "true",
        }

        # Add year filter if available
        if parsed.year:
            params["year"] = parsed.year

        from providers.net import fetch_json

        try:
            data, _final = await fetch_json(self, url, params=params, timeout=15)
            if not isinstance(data, dict):
                return []

            results = []
            for item in data.get("results", [])[:10]:
                title = item.get("title", "")
                orig_title = item.get("original_title", "")
                release_date = item.get("release_date", "")
                year = int(release_date[:4]) if release_date and len(release_date) >= 4 else None
                poster_path = item.get("poster_path", "")
                poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None

                results.append(SearchResult(
                    provider=self.name,
                    title=title,
                    year=year,
                    url=str(item.get("id", "")),
                    score=item.get("popularity", 0.0),
                    poster_url=poster_url,
                    extra={
                        "original_title": orig_title,
                        "overview": item.get("overview", ""),
                        "vote_average": item.get("vote_average", 0),
                        "genre_ids": item.get("genre_ids", []),
                    },
                ))

            return results

        except Exception as e:
            logger.error(f"[tmdb] Search error: {e}")
            return []

    async def get_detail(self, result: SearchResult) -> Optional[Metadata]:
        """Get full movie details from TMDB."""
        if not self._api_key or not result.url:
            return None

        movie_id = result.url
        url = f"{TMDB_API_BASE}/movie/{movie_id}"
        params = {
            "api_key": self._api_key,
            "language": self._language,
            "append_to_response": "credits,images",
        }

        from providers.net import fetch_json

        try:
            data, _final = await fetch_json(self, url, params=params, timeout=15)
            if not isinstance(data, dict):
                return None

            # Extract metadata
            title = data.get("title", result.title)
            original_title = data.get("original_title", "")
            release_date = data.get("release_date", "")
            year = int(release_date[:4]) if release_date and len(release_date) >= 4 else result.year
            runtime = data.get("runtime")
            overview = data.get("overview", "")
            vote_avg = data.get("vote_average", 0)
            tagline = data.get("tagline", "")

            # Poster & fanart
            poster_path = data.get("poster_path", "")
            poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else result.poster_url
            fanart_url = None
            backdrops = data.get("images", {}).get("backdrops", [])
            if backdrops:
                fanart_url = f"{TMDB_IMAGE_BASE}{backdrops[0].get('file_path', '')}"

            # Genres
            genres = [g.get("name", "") for g in data.get("genres", []) if g.get("name")]

            # Production companies (studio)
            studios = data.get("production_companies", [])
            studio = studios[0].get("name", "") if studios else None

            # Actors from credits
            actors = []
            credits_cast = data.get("credits", {}).get("cast", [])
            for member in credits_cast[:10]:
                actor_name = member.get("name", "")
                character = member.get("character", "")
                profile_path = member.get("profile_path", "")
                photo = f"{TMDB_IMAGE_BASE}{profile_path}" if profile_path else None
                if actor_name:
                    actors.append(Actor(name=actor_name, role=character, photo_url=photo))

            # Build metadata
            metadata = Metadata(
                title=title,
                original_title=original_title,
                year=year,
                actors=actors,
                genres=genres,
                overview=overview,
                runtime=runtime,
                rating=vote_avg if vote_avg else None,
                studio=studio,
                poster_url=poster_url,
                fanart_url=fanart_url,
                source_provider=self.name,
                source_url=f"https://www.themoviedb.org/movie/{movie_id}",
                extra={
                    "tagline": tagline,
                    "tmdb_id": movie_id,
                    "vote_count": data.get("vote_count", 0),
                    "original_language": data.get("original_language", ""),
                    "status": data.get("status", ""),
                },
            )

            return metadata

        except Exception as e:
            logger.error(f"[tmdb] Detail error: {e}")
            return None

    def _build_query(self, parsed: ParsedFilename) -> str:
        """Build search query for TMDB - use title, not code."""
        # TMDB doesn't know about Japanese AV codes
        if parsed.title and parsed.year:
            return f"{parsed.title} {parsed.year}"
        if parsed.title:
            return parsed.title
        return ""

    async def scrape(self, parsed: ParsedFilename) -> Optional[Metadata]:
        """Override scrape to use custom query building."""
        if not self.can_handle(parsed):
            return None

        query = self._build_query(parsed)
        if not query:
            logger.debug(f"[{self.name}] No query for: {parsed.original_name}")
            return None

        logger.info(f"[{self.name}] Searching: {query}")

        results = await self.search(query, parsed)
        if not results:
            logger.info(f"[{self.name}] No results: {query}")
            return None

        # Try to find best match
        best = results[0]

        # If we have a year, prefer exact year match
        if parsed.year:
            for r in results:
                if r.year == parsed.year:
                    best = r
                    break

        logger.info(f"[{self.name}] Match: {best.title} ({best.year})")

        metadata = await self.get_detail(best)
        if metadata:
            metadata.source_provider = self.name
            metadata.source_url = f"https://www.themoviedb.org/movie/{best.url}"
        return metadata
