import asyncio
import requests
from bs4 import BeautifulSoup
from typing import Optional
from ytmusicapi import YTMusic

def get_tidal_track_info(tidal_url):
    """Extrahiert Songtitel und Künstler anonym aus einem Tidal-Link"""
    headers = {
        # Ein echter User-Agent ist wichtig, damit Tidal die Anfrage nicht blockiert
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(tidal_url, headers=headers)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        
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

def convert_tidal_to_youtube(tidal_url):
    # 1. Infos von Tidal holen
    print("Extrahiere Song-Infos von Tidal...")
    search_query = get_tidal_track_info(tidal_url)
    
    if not search_query:
        return "Fehler: Song-Infos konnten von Tidal nicht gelesen werden."
    
    print(f"Suche auf YouTube Music nach: '{search_query}'")
    
    # 2. Auf YouTube Music suchen (ohne Login/Auth für die reine Suche)
    yt = YTMusic()
    search_results = yt.search(query=search_query, filter="songs")
    
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
    loop = asyncio.get_event_loop()
    query = await loop.run_in_executor(None, get_tidal_track_info, url)
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

def _search_ytmusic(url: str) -> Optional[str]:
    query = get_tidal_track_info(url)
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