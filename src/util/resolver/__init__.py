import re
from typing import Optional, List

from .apple_music import resolve as resolve_apple, resolve_direct as resolve_direct_apple, resolve_playlist as resolve_playlist_apple
from .deezer import resolve as resolve_deezer, resolve_direct as resolve_direct_deezer, resolve_playlist as resolve_playlist_deezer
from .spotify import resolve as resolve_spotify, resolve_direct as resolve_direct_spotify, resolve_playlist as resolve_playlist_spotify
from .tidal import resolve as resolve_tidal, resolve_direct as resolve_direct_tidal, resolve_playlist as resolve_playlist_tidal

SOURCE_NAMES = {
    "tidal.com": "Tidal",
    "music.apple.com": "Apple Music",
    "deezer.com": "Deezer",
    "open.spotify.com": "Spotify",
}

RESOLVERS = {
    "tidal.com": resolve_tidal,
    "music.apple.com": resolve_apple,
    "deezer.com": resolve_deezer,
    "open.spotify.com": resolve_spotify,
}

DIRECT_RESOLVERS = {
    "tidal.com": resolve_direct_tidal,
    "music.apple.com": resolve_direct_apple,
    "deezer.com": resolve_direct_deezer,
    "open.spotify.com": resolve_direct_spotify,
}

PLAYLIST_RESOLVERS = {
    "tidal.com": resolve_playlist_tidal,
    "music.apple.com": resolve_playlist_apple,
    "deezer.com": resolve_playlist_deezer,
    "open.spotify.com": resolve_playlist_spotify,
}

SUPPORTED_PATTERNS = {
    "tidal.com": re.compile(r'(stage\.)?tidal\.com/(browse/)?track/\d+(/u)?', re.I),
    "music.apple.com": re.compile(r'music\.apple\.com/\w+/(album/[\w-]+/\d+\?i=\d+|song/[\w-]+/\d+)', re.I),
    "deezer.com": re.compile(r'(link\.)?deezer\.com/(\w+/)?(track|s)/\w+', re.I),
    "open.spotify.com": re.compile(r'open\.spotify\.com/([a-z]{2,4}-[a-z]{2}/)?track/\w+', re.I),
}

def get_source_name(url: str) -> Optional[str]:
    for domain, name in SOURCE_NAMES.items():
        if domain in url.lower():
            return name
    return None

def needs_resolution(url: str) -> bool:
    for domain in RESOLVERS:
        if domain in url.lower():
            pattern = SUPPORTED_PATTERNS.get(domain)
            if pattern and pattern.search(url):
                return True
    return False

def is_playlist(url: str) -> bool:
    if re.search(r'(stage\.)?tidal\.com/(browse/)?(playlist|album)/', url, re.I):
        return True
    if re.search(r'music\.apple\.com/\w+/playlist/', url, re.I):
        return True
    if re.search(r'music\.apple\.com/\w+/album/', url, re.I) and '?i=' not in url:
        return True
    if re.search(r'(link\.)?deezer\.com/(\w+/)?(playlist|album)/', url, re.I):
        return True
    if re.search(r'open\.spotify\.com/([a-z]{2,4}-[a-z]{2}/)?(playlist|album)/', url, re.I):
        return True
    return False

async def resolve_to_search_query(url: str) -> Optional[dict]:
    domain_match = None
    for domain in RESOLVERS:
        if domain in url.lower():
            domain_match = domain
            break
    if not domain_match:
        return None

    resolver = RESOLVERS[domain_match]
    info = await resolver(url)
    if not info:
        return None

    info["is_playlist"] = is_playlist(url)
    return info

async def resolve_to_direct_url(url: str) -> Optional[str]:
    domain_match = None
    for domain in DIRECT_RESOLVERS:
        if domain in url.lower():
            domain_match = domain
            break
    if not domain_match:
        return None
    resolver = DIRECT_RESOLVERS[domain_match]
    return await resolver(url)

async def resolve_playlist(url: str) -> Optional[List[dict]]:
    domain_match = None
    for domain in PLAYLIST_RESOLVERS:
        if domain in url.lower():
            domain_match = domain
            break
    if not domain_match:
        return None
    resolver = PLAYLIST_RESOLVERS[domain_match]
    return await resolver(url)
