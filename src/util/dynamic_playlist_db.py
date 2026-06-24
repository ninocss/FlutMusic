import json
import os
import asyncio
import re
from typing import Dict, List, Optional

import yt_dlp

from util.resolver import is_playlist, resolve_playlist, get_source_name
from util.constants import YT_OPTS

DYNAMIC_FILE = "dynamic_playlists.json"
lock = asyncio.Lock()

YOUTUBE_PLAYLIST_RE = re.compile(
    r'(youtube\.com|youtu\.be)/(playlist\?list=|watch\?.*list=)', re.I
)

async def _load() -> Dict:
    async with lock:
        if not os.path.exists(DYNAMIC_FILE):
            return {}
        try:
            with open(DYNAMIC_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

async def _save(data: Dict):
    async with lock:
        with open(DYNAMIC_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

def detect_source_type(url: str) -> Optional[str]:
    domain = get_source_name(url)
    if domain:
        return domain.lower()
    if YOUTUBE_PLAYLIST_RE.search(url):
        return "youtube"
    return None

async def create_dynamic_playlist(user_id: str, name: str, source_url: str) -> tuple[bool, str]:
    data = await _load()
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = {}

    if name in data[user_id]:
        return False, "A dynamic playlist with that name already exists."

    source_type = detect_source_type(source_url)
    if not source_type:
        return False, "Unsupported source URL. Supported: Spotify, YouTube, Tidal, Deezer, Apple Music playlists."

    if not is_playlist(source_url) and source_type != "youtube":
        return False, "The URL does not appear to be a playlist/album URL."

    data[user_id][name] = {
        "source_url": source_url,
        "source_type": source_type
    }
    await _save(data)
    return True, f"Dynamic playlist **{name}** created."

async def delete_dynamic_playlist(user_id: str, name: str) -> bool:
    data = await _load()
    user_id = str(user_id)
    if user_id in data and name in data[user_id]:
        del data[user_id][name]
        await _save(data)
        return True
    return False

async def get_dynamic_playlist(user_id: str, name: str) -> Optional[Dict]:
    data = await _load()
    user_id = str(user_id)
    entry = data.get(user_id, {}).get(name)
    if entry is None:
        return None
    return entry

async def get_user_dynamic_playlists(user_id: str) -> List[Dict]:
    data = await _load()
    user_id = str(user_id)
    entries = data.get(user_id, {})
    return [
        {"name": name, **info}
        for name, info in entries.items()
    ]

async def fetch_dynamic_tracks(source_url: str, source_type: str) -> List[Dict]:
    if source_type == "youtube":
        opts = {**YT_OPTS, "extract_flat": True, "quiet": True}
        def run_yt():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(source_url, download=False)
        info = await asyncio.to_thread(run_yt)
        tracks = []
        if "entries" in info:
            for entry in info["entries"]:
                if entry and entry.get("url"):
                    tracks.append({
                        "url": entry["url"],
                        "title": entry.get("title", "Unknown"),
                    })
        return tracks

    resolved = await resolve_playlist(source_url)
    if resolved:
        return [
            {"url": t["url"], "title": t.get("title", "Unknown")}
            for t in resolved if t.get("url")
        ]
    return []
