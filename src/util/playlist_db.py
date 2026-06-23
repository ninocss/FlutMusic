import json
import os
import asyncio
from typing import Dict, List, Optional

PLAYLIST_FILE = "playlists.json"
lock = asyncio.Lock()

async def _load_playlists() -> Dict:
    async with lock:
        if not os.path.exists(PLAYLIST_FILE):
            return {}
        try:
            with open(PLAYLIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

async def _save_playlists(data: Dict):
    async with lock:
        with open(PLAYLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

async def create_playlist(user_id: str, name: str) -> bool:
    """Create a new playlist for the user."""
    data = await _load_playlists()
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = {}
    
    if name in data[user_id]:
        return False
        
    data[user_id][name] = []
    await _save_playlists(data)
    return True

async def delete_playlist(user_id: str, name: str) -> bool:
    """Delete a user's playlist."""
    data = await _load_playlists()
    user_id = str(user_id)
    if user_id in data and name in data[user_id]:
        del data[user_id][name]
        await _save_playlists(data)
        return True
    return False

async def add_to_playlist(user_id: str, name: str, url: str, title: str) -> bool:
    """Add a song to a playlist."""
    data = await _load_playlists()
    user_id = str(user_id)
    if user_id not in data or name not in data[user_id]:
        return False
        
    data[user_id][name].append({"url": url, "title": title})
    await _save_playlists(data)
    return True

async def import_playlist_tracks(user_id: str, name: str, tracks: List[Dict]) -> bool:
    """Import multiple tracks into a playlist."""
    data = await _load_playlists()
    user_id = str(user_id)
    if user_id not in data or name not in data[user_id]:
        return False
        
    data[user_id][name].extend(tracks)
    await _save_playlists(data)
    return True

async def get_playlist(user_id: str, name: str) -> Optional[List[Dict]]:
    """Get all tracks in a playlist."""
    data = await _load_playlists()
    user_id = str(user_id)
    return data.get(user_id, {}).get(name)

async def get_user_playlists(user_id: str) -> List[str]:
    """Get names of all playlists for a user."""
    data = await _load_playlists()
    user_id = str(user_id)
    return list(data.get(user_id, {}).keys())
