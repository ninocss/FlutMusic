import asyncio
import requests
from bs4 import BeautifulSoup
from typing import Optional
from ytmusicapi import YTMusic

def get_spotify_track_info(spotify_url):
    # Täuscht einen echten Browser vor
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(spotify_url, headers=headers)
    
    if response.status_code != 200:
        return None

    # HTML parsen
    soup = BeautifulSoup(response.text, 'html.parser')
    
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

def convert_link(spotify_url):
    print("Extrahiere Song-Infos von Spotify...")
    search_query = get_spotify_track_info(spotify_url)
    
    if not search_query:
        return "Fehler: Song-Infos konnten nicht von Spotify gelesen werden."
    
    print(f"Suche auf YouTube Music nach: '{search_query}'")
    
    # YTMusic ohne auth.json initialisieren (nur für die Suche reicht das!)
    yt = YTMusic()
    search_results = yt.search(query=search_query, filter="songs")
    
    if search_results:
        video_id = search_results[0]['videoId']
        return f"https://music.youtube.com/watch?v={video_id}"
    else:
        return "Song wurde auf YouTube Music nicht gefunden."

async def resolve(url: str) -> Optional[dict]:
    loop = asyncio.get_event_loop()
    query = await loop.run_in_executor(None, get_spotify_track_info, url)
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

def _search_ytmusic(url: str) -> Optional[str]:
    query = get_spotify_track_info(url)
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