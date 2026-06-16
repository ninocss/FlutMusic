import aiohttp
import re
import json
from html import unescape
from typing import Optional

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

SOURCE_NAMES = {
    "tidal.com": "Tidal",
    "music.apple.com": "Apple Music",
    "deezer.com": "Deezer",
    "open.spotify.com": "Spotify",
}

async def fetch_page(url: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={'User-Agent': USER_AGENT}
        ) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    return await resp.text()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
    return None

def parse_tidal(html_source: str) -> Optional[dict]:
    og_m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_source)
    if og_m:
        og_title = unescape(og_m.group(1))
        parts = [p.strip() for p in og_title.split(' - ', 1)]
        if len(parts) >= 2:
            return {"title": parts[1], "artist": parts[0]}
    title_m = re.search(r'<title>(.*?)</title>', html_source, re.DOTALL)
    if title_m:
        title_text = unescape(title_m.group(1).strip())
        title_text = re.sub(r'\s*\|\s*TIDAL$', '', title_text, flags=re.I).strip()
        m = re.match(r'^(.+?)\s+by\s+(.+?)\s+on\s+TIDAL\s*$', title_text, re.I)
        if m:
            return {"title": m.group(1).strip(), "artist": m.group(2).strip()}
        m = re.match(r'^(.+?)\s+·\s+(.+)$', title_text)
        if m:
            return {"title": m.group(1).strip(), "artist": m.group(2).strip()}
    m = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html_source, re.DOTALL)
    if m:
        try:
            ld = json.loads(m.group(1))
            if isinstance(ld, dict):
                name = ld.get("name", "")
                by = ld.get("byArtist", {})
                if isinstance(by, dict):
                    artist = by.get("name", "")
                else:
                    artist = str(by) if by else ""
                if name:
                    return {"title": name, "artist": artist}
        except Exception:
            pass
    return None

def parse_apple_music(html_source: str) -> Optional[dict]:
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_source)
    if m:
        og_title = unescape(m.group(1))
        m2 = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html_source)
        description = unescape(m2.group(1)) if m2 else ""
        parts = [p.strip() for p in og_title.split(' - ')]
        if len(parts) >= 2:
            return {"title": parts[1], "artist": parts[0]}
        return {"title": og_title, "artist": description.split(',')[0].strip() if description else ""}
    return None

def parse_deezer(html_source: str) -> Optional[dict]:
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html_source)
    if m:
        og_title = unescape(m.group(1))
        parts = [p.strip() for p in og_title.split(' - ')]
        if len(parts) >= 2:
            return {"title": parts[1], "artist": parts[0]}
        return {"title": og_title, "artist": ""}
    return None

PARSERS = {
    "tidal.com": parse_tidal,
    "music.apple.com": parse_apple_music,
    "deezer.com": parse_deezer,
}

SUPPORTED_PATTERNS = {
    "tidal.com": re.compile(r'tidal\.com/(browse/)?track/\d+', re.I),
    "music.apple.com": re.compile(r'music\.apple\.com/\w+/album/[\w-]+/\d+\?i=\d+', re.I),
    "deezer.com": re.compile(r'deezer\.com/(\w+/)?track/\d+', re.I),
}

def needs_resolution(url: str) -> bool:
    for domain in PARSERS:
        if domain in url.lower():
            pattern = SUPPORTED_PATTERNS.get(domain)
            if pattern and pattern.search(url):
                return True
    return False

def is_playlist(url: str) -> bool:
    if re.search(r'tidal\.com/(browse/)?(playlist|album)/', url, re.I):
        return True
    if re.search(r'music\.apple\.com/\w+/playlist/', url, re.I):
        return True
    if re.search(r'music\.apple\.com/\w+/album/', url, re.I) and '?i=' not in url:
        return True
    if re.search(r'deezer\.com/(\w+/)?(playlist|album)/', url, re.I):
        return True
    return False

async def resolve_to_search_query(url: str) -> Optional[dict]:
    domain_match = None
    for domain in PARSERS:
        if domain in url.lower():
            domain_match = domain
            break
    if not domain_match:
        return None

    page = await fetch_page(url)
    if not page:
        return None

    parser = PARSERS[domain_match]
    info = parser(page)
    if not info or not info.get("title"):
        return None

    title = info["title"]
    artist = info.get("artist", "")
    query_parts = [p for p in [artist, title] if p]
    query = " - ".join(query_parts)
    return {
        "query": f"ytsearch:{query}",
        "title": title,
        "artist": artist,
        "source_name": SOURCE_NAMES.get(domain_match, domain_match),
        "is_playlist": is_playlist(url),
    }
