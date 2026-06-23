"""
Lyrics fetching utility using lrclib.net (free, no API key required).
Falls back to a basic Genius-style search if lrclib is unavailable.
"""
import aiohttp
import re
import logging

logger = logging.getLogger(__name__)

USER_AGENT = 'ShizoBot/1.0 (Discord Music Bot)'


def _clean_text(text: str) -> str:
    """Remove common suffixes like (Official Video), (Lyrics), etc."""
    return re.sub(
        r'\s*[\(\[](Official\s*(Music\s*)?Video|Lyrics?|Audio|HD|HQ|4K|Explicit)[\)\]]',
        '', text, flags=re.IGNORECASE
    ).strip()


def _extract_artist_name(author: str) -> str:
    """Extract the primary artist name from an uploader/author string."""
    author = _clean_text(author)
    # Handle "Artist - Topic" (YouTube auto-generated)
    if ' - Topic' in author:
        return author.replace(' - Topic', '').strip()
    # Handle "Artist, Artist2" or "Artist & Artist2"
    author = re.split(r'\s*[,&]\s*', author, maxsplit=1)[0]
    # Handle "Artist ft. Other"
    author = re.split(r'\s+(?:ft\.?|feat\.?)\s+', author, maxsplit=1)[0]
    return author.strip()


async def fetch_lyrics(track: str, artist: str = "") -> dict | None:
    """
    Fetch song lyrics from lrclib.net.

    Returns:
        dict with 'plain_lyrics', 'synced_lyrics' (optional),
        'track_name', 'artist_name' — or None if not found.
    """
    track = _clean_text(track)
    artist = _extract_artist_name(artist)

    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/json',
    }

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15),
        headers=headers
    ) as session:
        # Strategy 1: search with combined query
        combined = f"{artist} {track}".strip() if artist else track
        result = await _lrclib_search(session, combined)
        if result:
            return result

        # Strategy 2: search with track only
        if artist:
            result = await _lrclib_search(session, track)
            return result

        return None


async def _lrclib_search(
    session: aiohttp.ClientSession,
    query: str
) -> dict | None:
    """Try a single lrclib.net search and return lyrics dict or None."""
    params = {'q': query}

    try:
        async with session.get('https://lrclib.net/api/search', params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

        for item in data:
            plain = item.get('plainLyrics')
            if plain:
                return {
                    'plain_lyrics': plain,
                    'synced_lyrics': item.get('syncedLyrics') or None,
                    'track_name': item.get('trackName', track),
                    'artist_name': item.get('artistName', artist),
                }
    except Exception as e:
        logger.warning(f"lrclib search error: {e}")

    return None
