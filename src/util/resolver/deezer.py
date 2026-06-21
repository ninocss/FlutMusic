import asyncio
import requests
from bs4 import BeautifulSoup
from typing import Optional
from ytmusicapi import YTMusic

def get_deezer_track_info(deezer_url):
    """Extrahiert Songtitel und Künstler anonym aus einem Deezer-Link"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7" # Zwingt Deezer auf Deutsch für sauberes Parsing
    }
    
    try:
        response = requests.get(deezer_url, headers=headers)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        
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

def convert_deezer_to_youtube(deezer_url):
    # 1. Infos von Deezer holen
    print("Extrahiere Song-Infos von Deezer...")
    search_query = get_deezer_track_info(deezer_url)
    
    if not search_query:
        return "Fehler: Song-Infos konnten von Deezer nicht gelesen werden."
    
    print(f"Suche auf YouTube Music nach: '{search_query}'")
    
    # 2. Auf YouTube Music suchen
    yt = YTMusic()
    search_results = yt.search(query=search_query, filter="songs")
    
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
    loop = asyncio.get_event_loop()
    query = await loop.run_in_executor(None, get_deezer_track_info, url)
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

def _search_ytmusic(url: str) -> Optional[str]:
    query = get_deezer_track_info(url)
    if not query:
        return None
    yt = YTMusic()
    results = yt.search(query=query, filter="songs")
    if results:
        return f"https://music.youtube.com/watch?v={results[0]['videoId']}"
    return None

async def resolve_direct(url: str) -> Optional[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _search_ytmusic, url)