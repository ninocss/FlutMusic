import asyncio
import re
import aiohttp
from bs4 import BeautifulSoup
from typing import Optional, List
from ytmusicapi import YTMusic

async def get_deezer_track_info(deezer_url):
    """Extrahiert Songtitel und Künstler anonym aus einem Deezer-Link"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7" # Zwingt Deezer auf Deutsch für sauberes Parsing
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(deezer_url, headers=headers) as response:
                if response.status != 200:
                    return None
                html = await response.text()

        soup = BeautifulSoup(html, 'html.parser')
        
        # Deezer nutzt ebenfalls OpenGraph-Tags
        title_tag = soup.find("meta", property="og:title")
        
        if title_tag:
            full_title = title_tag["content"]
            
            # Deezer formatiert den og:title meistens als: "Künstler - Songtitel - Musik hören"
            # oder bei kürzeren Links einfach "Künstler - Songtitel"
            if " - Musik hören" in full_title:
                full_title = full_title.replace(" - Musik hören", "")
            
            # Umdrehen oder Säubern (YT Music versteht "Künstler - Songtitel" perfekt)
            return full_title
            
    except Exception as e:
        print(f"Fehler beim Scraping von Deezer: {e}")
        
    return None

async def convert_deezer_to_youtube(deezer_url):
    # 1. Infos von Deezer holen
    print("Extrahiere Song-Infos von Deezer...")
    search_query = await get_deezer_track_info(deezer_url)
    
    if not search_query:
        return "Fehler: Song-Infos konnten von Deezer nicht gelesen werden."
    
    print(f"Suche auf YouTube Music nach: '{search_query}'")
    
    # 2. Auf YouTube Music suchen
    loop = asyncio.get_event_loop()
    yt = YTMusic()
    search_results = await loop.run_in_executor(None, lambda: yt.search(query=search_query, filter="songs"))
    
    # 3. Ergebnis auswerten
    if search_results:
        video_id = search_results[0]['videoId']
        yt_music_link = f"https://music.youtube.com/watch?v={video_id}"
        
        title = search_results[0].get('title', 'Unbekannt')
        artist = search_results[0]['artists'][0]['name'] if search_results[0].get('artists') else 'Unbekannt'
        
        return f"Erfolg! Gefunden: '{title}' von '{artist}'\nLink: {yt_music_link}"
    else:
        return "Song wurde auf YouTube Music leider nicht gefunden."

async def resolve(url: str) -> Optional[dict]:
    query = await get_deezer_track_info(url)
    if not query:
        return None
    parts = [p.strip() for p in query.replace(" - ", "||").split("||")]
    if len(parts) >= 2:
        title = parts[1]
        artist = parts[0]
    else:
        title = query
        artist = ""
    return {
        "query": f"ytsearch:{query}",
        "title": title,
        "artist": artist,
        "source_name": "Deezer",
    }

async def resolve_direct(url: str) -> Optional[str]:
    query = await get_deezer_track_info(url)
    if not query:
        return None
    loop = asyncio.get_event_loop()
    yt = YTMusic()
    results = await loop.run_in_executor(None, lambda: yt.search(query=query, filter="songs"))
    if results:
        return f"https://music.youtube.com/watch?v={results[0]['videoId']}"
    return None

async def get_deezer_playlist_tracks(url: str) -> Optional[List[str]]:
    id_match = re.search(r'(?:link\.)?deezer\.com/(?:\w+/)?(playlist|album)/(\d+)', url, re.I)
    if not id_match:
        return None

    content_type = id_match.group(1)
    content_id = id_match.group(2)
    api_url = f"https://api.deezer.com/{content_type}/{content_id}/tracks"

    track_urls = []
    seen = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    try:
        async with aiohttp.ClientSession() as session:
            next_url: Optional[str] = api_url
            while next_url and len(track_urls) < 50:
                async with session.get(next_url, headers=headers) as response:
                    if response.status != 200:
                        break
                    data = await response.json()

                for item in data.get("data", []):
                    track_id = item.get("id")
                    if track_id and track_id not in seen:
                        seen.add(track_id)
                        track_urls.append(f"https://deezer.com/track/{track_id}")

                next_url = data.get("next")
        return track_urls[:50] if track_urls else None
    except Exception as e:
        print(f"Deezer API error: {e}")
        return None

async def resolve_playlist(url: str) -> Optional[List[dict]]:
    track_urls = await get_deezer_playlist_tracks(url)
    if not track_urls:
        return None
    tasks = [resolve(t) for t in track_urls]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r] or None