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

async def create_playlist(guild_id: str, name: str) -> bool:
    """Create a new playlist for the server."""
    data = await _load_playlists()
    guild_id = str(guild_id)
    if guild_id not in data:
        data[guild_id] = {}
    
    if name in data[guild_id]:
        return False
        
    data[guild_id][name] = []
    await _save_playlists(data)
    return True

async def delete_playlist(guild_id: str, name: str) -> bool:
    """Delete a server's playlist."""
    data = await _load_playlists()
    guild_id = str(guild_id)
    if guild_id in data and name in data[guild_id]:
        del data[guild_id][name]
        await _save_playlists(data)
        return True
    return False

async def add_to_playlist(guild_id: str, name: str, url: str, title: str) -> bool:
    """Add a song to a playlist."""
    data = await _load_playlists()
    guild_id = str(guild_id)
    if guild_id not in data or name not in data[guild_id]:
        return False
        
    data[guild_id][name].append({"url": url, "title": title})
    await _save_playlists(data)
    return True

async def import_playlist_tracks(guild_id: str, name: str, tracks: List[Dict]) -> bool:
    """Import multiple tracks into a playlist."""
    data = await _load_playlists()
    guild_id = str(guild_id)
    if guild_id not in data or name not in data[guild_id]:
        return False
        
    data[guild_id][name].extend(tracks)
    await _save_playlists(data)
    return True

async def get_playlist(guild_id: str, name: str) -> Optional[List[Dict]]:
    """Get all tracks in a playlist."""
    data = await _load_playlists()
    guild_id = str(guild_id)
    return data.get(guild_id, {}).get(name)

async def get_guild_playlists(guild_id: str) -> List[str]:
    """Get names of all playlists for a server."""
    data = await _load_playlists()
    guild_id = str(guild_id)
    return list(data.get(guild_id, {}).keys())
