import asyncio
import re
import aiohttp
from bs4 import BeautifulSoup
from typing import Optional, List
from ytmusicapi import YTMusic

async def get_tidal_track_info(tidal_url):
    """Extrahiert Songtitel und Künstler anonym aus einem Tidal-Link"""
    headers = {
        # Ein echter User-Agent ist wichtig, damit Tidal die Anfrage nicht blockiert
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(tidal_url, headers=headers) as response:
                if response.status != 200:
                    return None
                html = await response.text()

        soup = BeautifulSoup(html, 'html.parser')
        
        # Tidal nutzt standardmäßig OpenGraph-Meta-Tags für Titel und Beschreibung
        title_tag = soup.find("meta", property="og:title")
        
        if title_tag:
            # Der Titel bei Tidal sieht meist so aus: "Songtitel von Künstler"
            full_title = title_tag["content"]
            
            # Das "von" trennen, um eine saubere Suchanfrage zu haben
            if " von " in full_title:
                search_query = full_title.replace(" von ", " ")
                return search_query
            return full_title
            
    except Exception as e:
        print(f"Fehler beim Scraping von Tidal: {e}")
        
    return None

async def convert_tidal_to_youtube(tidal_url):
    # 1. Infos von Tidal holen
    print("Extrahiere Song-Infos von Tidal...")
    search_query = await get_tidal_track_info(tidal_url)
    
    if not search_query:
        return "Fehler: Song-Infos konnten von Tidal nicht gelesen werden."
    
    print(f"Suche auf YouTube Music nach: '{search_query}'")
    
    # 2. Auf YouTube Music suchen (ohne Login/Auth für die reine Suche)
    loop = asyncio.get_event_loop()
    yt = YTMusic()
    search_results = await loop.run_in_executor(None, lambda: yt.search(query=search_query, filter="songs"))
    
    # 3. Ergebnis auswerten
    if search_results:
        video_id = search_results[0]['videoId']
        yt_music_link = f"https://music.youtube.com/watch?v={video_id}"
        
        # Optionale Infos für die Konsole
        title = search_results[0].get('title', 'Unbekannt')
        artist = search_results[0]['artists'][0]['name'] if search_results[0].get('artists') else 'Unbekannt'
        
        return f"Erfolg! Gefunden: '{title}' von '{artist}'\nLink: {yt_music_link}"
    else:
        return "Song wurde auf YouTube Music leider nicht gefunden."

async def resolve(url: str) -> Optional[dict]:
    query = await get_tidal_track_info(url)
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
        "source_name": "Tidal",
    }

async def resolve_direct(url: str) -> Optional[str]:
    query = await get_tidal_track_info(url)
    if not query:
        return None
    loop = asyncio.get_event_loop()
    yt = YTMusic()
    results = await loop.run_in_executor(None, lambda: yt.search(query=query, filter="songs"))
    if results:
        return f"https://music.youtube.com/watch?v={results[0]['videoId']}"
    return None

async def get_tidal_playlist_tracks(url: str) -> Optional[List[str]]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    playlist_id_match = re.search(
        r'tidal\.com/(?:browse/)?(?:playlist|album)/([a-f0-9\-]{36}|\d+)', url, re.I
    )
    if not playlist_id_match:
        return None
    playlist_id = playlist_id_match.group(1)

    is_uuid = '-' in playlist_id
    if is_uuid:
        api_url = f"https://listen.tidal.com/v1/playlists/{playlist_id}/tracks"
    else:
        api_url = f"https://listen.tidal.com/v1/albums/{playlist_id}/tracks"

    params = {"countryCode": "US", "limit": 50}
    api_headers = {**headers, "X-Tidal-Token": "CzET4vdadNUFQ5JU"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=api_headers, params=params) as response:
                if response.status != 200:
                    return None
                data = await response.json()

        items = data.get("items", [])
        track_urls = []
        for item in items:
            track = item.get("item", item)
            track_id = track.get("id")
            if track_id:
                track_urls.append(f"https://tidal.com/browse/track/{track_id}")
        return track_urls if track_urls else None

    except Exception as e:
        print(f"Tidal API error: {e}")
        return None

async def resolve_playlist(url: str) -> Optional[List[dict]]:
    track_urls = await get_tidal_playlist_tracks(url)
    if not track_urls:
        return None
    tasks = [resolve(t) for t in track_urls]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r] or None