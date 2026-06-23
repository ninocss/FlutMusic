import asyncio
import re
import aiohttp
from bs4 import BeautifulSoup
from typing import Optional, List
from ytmusicapi import YTMusic

async def get_spotify_track_info(spotify_url):
    # Täuscht einen echten Browser vor
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with aiohttp.ClientSession() as session:
        async with session.get(spotify_url, headers=headers) as response:
            if response.status != 200:
                return None
            html = await response.text()

    # HTML parsen
    soup = BeautifulSoup(html, 'html.parser')
    
    # Spotify speichert den Titel oft im <title> Tag oder in OpenGraph Meta-Tags
    title_tag = soup.find("meta", property="og:title")
    description_tag = soup.find("meta", property="og:description")
    
    if title_tag and description_tag:
        song_title = title_tag["content"]
        # Die Description enthält oft "Song von [Künstler] · [Jahr]"
        artist_info = description_tag["content"].split("·")[0].replace("Song von", "").strip()
        return f"{song_title} {artist_info}"
    
    # Fallback, falls die Meta-Tags fehlen (z.B. aus dem Seitentitel)
    elif soup.title:
        # Der Titel ist meistens "Song-Name - song and lyrics by Künstler | Spotify"
        clean_title = soup.title.string.replace("| Spotify", "").strip()
        return clean_title

    return None

async def convert_link(spotify_url):
    print("Extrahiere Song-Infos von Spotify...")
    search_query = await get_spotify_track_info(spotify_url)
    
    if not search_query:
        return "Fehler: Song-Infos konnten nicht von Spotify gelesen werden."
    
    print(f"Suche auf YouTube Music nach: '{search_query}'")
    
    # YTMusic ohne auth.json initialisieren (nur für die Suche reicht das!)
    loop = asyncio.get_event_loop()
    yt = YTMusic()
    search_results = await loop.run_in_executor(None, lambda: yt.search(query=search_query, filter="songs"))
    
    if search_results:
        video_id = search_results[0]['videoId']
        return f"https://music.youtube.com/watch?v={video_id}"
    else:
        return "Song wurde auf YouTube Music nicht gefunden."

async def resolve(url: str) -> Optional[dict]:
    query = await get_spotify_track_info(url)
    if not query:
        return None
    parts = [p.strip() for p in query.replace(" - ", "||").split("||")]
    if len(parts) >= 2:
        title = parts[1] if len(parts) >= 2 else parts[0]
        artist = parts[0]
    else:
        title = query
        artist = ""
    return {
        "query": f"ytsearch:{query}",
        "title": title,
        "artist": artist,
        "source_name": "Spotify",
    }

async def resolve_direct(url: str) -> Optional[str]:
    query = await get_spotify_track_info(url)
    if not query:
        return None
    loop = asyncio.get_event_loop()
    yt = YTMusic()
    results = await loop.run_in_executor(None, lambda: yt.search(query=query, filter="songs"))
    if results:
        return f"https://music.youtube.com/watch?v={results[0]['videoId']}"
    return None

async def get_spotify_playlist_tracks(url: str) -> Optional[List[str]]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return None
            html = await response.text()
            
    soup = BeautifulSoup(html, 'html.parser')
    track_urls = []
    seen = set()
    track_pattern = re.compile(r'open\.spotify\.com/([a-z]{2,4}-[a-z]{2}/)?track/\w+', re.I)
    for link in soup.find_all('a', href=True):
        href = link['href']
        match = track_pattern.search(href)
        if match:
            full_url = match.group(0)
            if not full_url.startswith('http'):
                full_url = 'https://' + full_url
            if full_url not in seen:
                seen.add(full_url)
                track_urls.append(full_url)
    # Also look for track data in script tags
    for script in soup.find_all('script'):
        if script.string and 'Spotify.Entity' in script.string:
            for match in track_pattern.finditer(script.string):
                full_url = match.group(0)
                if not full_url.startswith('http'):
                    full_url = 'https://' + full_url
                if full_url not in seen:
                    seen.add(full_url)
                    track_urls.append(full_url)
    return track_urls[:50] if track_urls else None

async def resolve_playlist(url: str) -> Optional[List[dict]]:
    track_urls = await get_spotify_playlist_tracks(url)
    if not track_urls:
        return None
    tasks = [resolve(t) for t in track_urls]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r] or None