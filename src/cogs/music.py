# ruff: noqa: F403 F405
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import random
import concurrent.futures
from typing import List, Optional, Tuple
from cachetools import TTLCache
from util.constants import *
from util.music.queue import *
from modals.embeds import *
from lang.texts import *
from util.resolver import (
    resolve_to_search_query, resolve_to_direct_url, resolve_playlist,
    needs_resolution, is_playlist, get_source_name
)
from views.musicviews import ActionsView, LyricsButtonView, QueueView, NowPlayingView
import json

from util.playlist_db import (
    create_playlist, delete_playlist, add_to_playlist,
    import_playlist_tracks, get_playlist, get_guild_playlists
)
from util.dynamic_playlist_db import (
    create_dynamic_playlist, delete_dynamic_playlist,
    get_dynamic_playlist, get_user_dynamic_playlists,
    fetch_dynamic_tracks
)
import os
from datetime import datetime, timedelta, timezone
from ytmusicapi import YTMusic
import re

import time
import threading
from collections import deque
from util.lyrics import fetch_lyrics
import logging
import colorlog

_handler = colorlog.StreamHandler()
_handler.setFormatter(colorlog.ColoredFormatter(
    '%(name_log_color)s%(name)s%(reset)s: [%(levelname)s] %(message_log_color)s%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG': 'cyan', 'INFO': 'cyan', 'WARNING': 'yellow',
        'ERROR': 'red', 'CRITICAL': 'red,bg_white',
    },
    secondary_log_colors={
        'message': {'DEBUG': 'white', 'INFO': 'white', 'WARNING': 'white', 'ERROR': 'white', 'CRITICAL': 'white'},
        'name': {'DEBUG': 'light_black', 'INFO': 'light_black', 'WARNING': 'light_black', 'ERROR': 'light_black', 'CRITICAL': 'light_black'},
    }
))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger(__name__)

guild_queues = {}
guild_volumes = {}

AUTOPLAY_URL_RE = re.compile(r"(https?://[^\s)]+)", re.IGNORECASE)
YT_PLAYLIST_RE = re.compile(r'(?:youtube|music\.youtube|youtu\.be).*(?:/playlist|list=)', re.I)

MUSIC_SOURCES = {
    "spotify":   {"domain": "open.spotify.com",     "label": "Spotify",     "icon": "🎧", "extractor": "spotify"},
    "deezer":    {"domain": "deezer.com",           "label": "Deezer",     "icon": "🎵", "extractor": "deezer"},
    "tidal":     {"domain": "tidal.com",            "label": "Tidal",      "icon": "🌊", "extractor": "tidal"},
    "soundcloud":{"domain": "soundcloud.com",       "label": "SoundCloud", "icon": "☁️", "extractor": "soundcloud"},
    "youtube":   {"domain": "youtube.com",          "label": "YouTube",    "icon": "▶️", "extractor": "youtube"},
    "youtubemusic":{"domain": "music.youtube.com",  "label": "YouTube Music", "icon": "🎵", "extractor": "youtube"},
    "youtu":     {"domain": "youtu.be",             "label": "YouTube",    "icon": "▶️", "extractor": "youtube"},
    "applemusic":{"domain": "music.apple.com",      "label": "Apple Music","icon": "🍎", "extractor": "apple"},
    "bandcamp":  {"domain": "bandcamp.com",         "label": "Bandcamp",   "icon": "🏕️", "extractor": "bandcamp"},
    "twitch":    {"domain": "twitch.tv",            "label": "Twitch",     "icon": "📺", "extractor": "twitch"},
    "vimeo":     {"domain": "vimeo.com",            "label": "Vimeo",      "icon": "🎬", "extractor": "vimeo"},
}

SEARCH_PREFIXES = {
    "youtube":    "ytsearch",
    "soundcloud": "scsearch",
    "youtubemusic": "ytmsearch",
    "youtu":      "ytsearch",
}

SOURCE_CHOICES = [
    app_commands.Choice(name="Auto (default)", value="auto"),
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="SoundCloud", value="soundcloud"),
    app_commands.Choice(name="Spotify (URL only)", value="spotify"),
    app_commands.Choice(name="Deezer (URL only)", value="deezer"),
    app_commands.Choice(name="Tidal (URL only)", value="tidal"),
    app_commands.Choice(name="Apple Music (URL only)", value="applemusic"),
]

def detect_source(url: str) -> dict:
    for key, source in MUSIC_SOURCES.items():
        domain = source["domain"]
        if domain in url.lower():
            return source
    return {"label": "Unknown", "icon": "🔗", "extractor": None}

def detect_source_from_entry(entry: dict) -> dict:
    extractor = entry.get("extractor", "") or ""
    for key, source in MUSIC_SOURCES.items():
        if source["extractor"] and source["extractor"] in extractor.lower():
            return source
    webpage_url = entry.get("webpage_url", "")
    if webpage_url:
        return detect_source(webpage_url)
    return {"label": "Unknown", "icon": "🔗", "extractor": None}

def safe_avatar(user: discord.abc.User) -> Optional[str]:
    try:
        return user.display_avatar.url
    except Exception:
        return None

async def purge_channel(channel: discord.TextChannel, limit: Optional[int] = None, check=None):
    """Delete all messages in a channel
    Args:
        channel: The Discord text channel to purge
        limit: Maximum number of messages to delete (None for unlimited)
        check: Optional function to filter messages (msg -> bool)
    
    Returns:
        int: Number of messages deleted
    """
    deleted_count = 0
    
    try:
        messages = []
        async for msg in channel.history(limit=limit):
            if not check or check(msg):
                messages.append(msg)
        
        if not messages:
            return 0
        
        now = discord.utils.utcnow()
        recent_messages = [msg for msg in messages if (now - msg.created_at).days < 14]
        old_messages = [msg for msg in messages if (now - msg.created_at).days >= 14]
        
        while recent_messages:
            batch = recent_messages[:100]
            recent_messages = recent_messages[100:]
            
            try:
                if len(batch) >= 2:
                    await channel.delete_messages(batch)
                    deleted_count += len(batch)
                    logger.info(f"Bulk deleted {len(batch)} messages")
                elif len(batch) == 1:
                    await batch[0].delete()
                    deleted_count += 1
                
                if recent_messages:
                    await asyncio.sleep(2)
                    
            except discord.HTTPException as e:
                if e.status == 429:
                    retry_after = float(e.response.headers.get('Retry-After', 2))
                    logger.warning(f"Rate limited on bulk delete, waiting {retry_after:.1f}s")
                    await asyncio.sleep(retry_after)
                    recent_messages = batch + recent_messages
                else:
                    logger.error(f"Bulk delete failed: {e}")
                    for msg in batch:
                        try:
                            await msg.delete()
                            deleted_count += 1
                            await asyncio.sleep(1)
                        except Exception:
                            pass
        
        for msg in old_messages:
            try:
                await msg.delete()
                deleted_count += 1
                
                if deleted_count % 5 == 0:
                    await asyncio.sleep(5)
                else:
                    await asyncio.sleep(1)
                    
            except discord.Forbidden:
                logger.error("Missing permissions to delete messages")
                return deleted_count
            except discord.HTTPException as e:
                if e.status == 429:
                    retry_after = float(e.response.headers.get('Retry-After', 5))
                    logger.warning(f"Rate limited, waiting {retry_after:.1f}s")
                    await asyncio.sleep(retry_after)
                    try:
                        await msg.delete()
                        deleted_count += 1
                    except Exception:
                        pass
                else:
                    logger.error(f"Failed to delete message: {e}")
        
        logger.info(f"Purge complete: {deleted_count} messages deleted from #{channel.name}")
        
    except discord.Forbidden:
        logger.error(f"Bot doesn't have permission to read/delete messages in #{channel.name}")
    except Exception as e:
        logger.error(f"Error during channel purge: {e}")
    
    return deleted_count

class ThreadSafeTTLCache:
    def __init__(self, maxsize, ttl):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.RLock()

    def __contains__(self, key):
        with self._lock:
            return key in self._cache

    def get(self, key, default=None):
        with self._lock:
            return self._cache.get(key, default)

    def __getitem__(self, key):
        with self._lock:
            return self._cache[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._cache[key] = value

    def pop(self, key, default=None):
        with self._lock:
            return self._cache.pop(key, default)

_extract_cache = ThreadSafeTTLCache(maxsize=200, ttl=240)

class AsyncSongLoader:
    def __init__(self, max_workers=12):
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    async def extract_info_async(self, url: str, loop=None):
        cached = _extract_cache.get(url)
        if cached is not None:
            return cached

        if loop is None:
            loop = asyncio.get_running_loop()

        def run_yt():
            with yt_dlp.YoutubeDL(YT_OPTS) as ydl:
                return ydl.extract_info(url, download=False)

        result = await loop.run_in_executor(self.executor, run_yt)

        if result and 'url' in result:
            _extract_cache[url] = result
        return result

    async def extract_info_flat_async(self, url: str, loop=None):
        if loop is None:
            loop = asyncio.get_running_loop()

        def run_yt():
            flat_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'ignoreerrors': True,
            }
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                return ydl.extract_info(url, download=False)

        return await loop.run_in_executor(self.executor, run_yt)

    async def preload_audio_source(self, stream_url: str, loop=None):
        if loop is None:
            loop = asyncio.get_running_loop()

        def create_source():
            ffmpeg_args = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                'options': '-vn -bufsize 512k'
            }
            return discord.FFmpegPCMAudio(stream_url, **ffmpeg_args)

        return await loop.run_in_executor(self.executor, create_source)

song_loader = AsyncSongLoader()

class OptimizedQueue:
    def __init__(self):
        self.queue = deque()
        self.playing = False
        self.lock = asyncio.Lock()
        self._loop_played = []
        self._last_playback_error = None

    def add(self, song_data):
        self.queue.append(song_data)

    def get_next(self):
        if self.queue:
            return self.queue.popleft()
        return None

    def peek(self):
        return self.queue[0] if self.queue else None

    def is_empty(self):
        return len(self.queue) == 0

    def clear(self):
        self.queue.clear()

class MusicCog(commands.Cog):
    playlist_group = app_commands.Group(name="playlist", description="Manage personal playlists")
    dynamic_playlist_group = app_commands.Group(name="dynamic-playlist", description="Manage dynamic playlists (live-fetched from source)")

    def __init__(self, bot):
        self.bot = bot
        self.bot_start_time = discord.utils.utcnow()
        self.songs_played = 0
        self.background_tasks = set()
        self._progress_tasks = {}
        self.currently_playing = {}  # guild_id -> {song_data, started_at, elapsed, is_paused}
        self.guild_loops = {}  # guild_id -> 'off' | 'song' | 'queue'
        self._play_next_locks = {}  # guild_id -> asyncio.Lock
        self._now_playing_messages = {}  # guild_id -> discord.Message
        self._timeout_cache = {}  # user_id -> timeout_data (in-memory, synced to disk on change)
        self._load_timeouts()
        self._playlist_cache_time = 0
        self._playlist_cache_ttl = 3600  # 1 hour
        self._ytmusic = YTMusic()

    def make_embed(
        self,
        title: str,
        description: Optional[str] = None,
        *,
        color: int = 0x5865F2,
        thumbnail: Optional[str] = None,
        author_name: Optional[str] = None,
        author_icon: Optional[str] = None,
        footer: Optional[str] = None,
        footer_icon: Optional[str] = None,
        fields: Optional[List[Tuple[str, str, bool]]] = None,
    ) -> discord.Embed:
        embed = discord.Embed(title=title, description=description or "", color=color)
        embed.timestamp = discord.utils.utcnow()
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if author_name:
            embed.set_author(name=author_name, icon_url=author_icon or "")
        if footer:
            embed.set_footer(text=footer, icon_url=footer_icon or "")
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
        return embed

    def create_background_task(self, coro):
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def _delayed_cleanup_task(self, guild_id):
        try:
            await asyncio.sleep(120)
            
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client and guild.voice_client.is_connected():
                return
                
            channel = await self.bot.fetch_channel(I_CHANNEL)
            if channel:
                await purge_channel(channel)
                await self.send_static_message()
        except Exception as e:
            logger.error(f"Error during delayed cleanup: {e}")

    async def send_static_message(self):
        try:
            actions_embed = self.make_embed(
                title="Marcante Musik",
                description=(
                    "**/play <URL | search term>**\n"
                    "Play a song from YouTube, Spotify, SoundCloud, or other supported platforms.\n\n"
                    "**/queue**\n"
                    "View the current song queue.\n\n"
                    "**/skip**\n"
                    "Skip the currently playing song.\n\n" 
                ),
                color=0x5865F2,
                thumbnail=safe_avatar(self.bot.user),
                footer_icon=safe_avatar(self.bot.user),
                footer="Join a voice channel and start listening!",
                fields=[]
            )

            channel = await self.bot.fetch_channel(I_CHANNEL)
            if channel:
                def check(msg):
                    return (msg.author == self.bot.user and msg.embeds and msg.embeds[0].title and
                            ("Marcante Musik" in msg.embeds[0].title or "Music" in msg.embeds[0].title))
                try:
                    await channel.purge(limit=100, check=check, bulk=True)
                except discord.HTTPException:
                    pass

                await channel.send(embed=actions_embed, view=ActionsView(bot=self.bot))
        except Exception as e:
            logger.error(f"Error sending disconnect message: {e}")

    async def _retry_failed_song(self, guild, voice_client, interaction, song_data, retry_count=0):
        """Retry playing a song after a mid-playback error (e.g. 403 Forbidden)."""
        MAX_RETRIES = 2
        queue = guild_queues.get(guild.id)
        if not queue:
            return

        webpage_url = song_data.get('song_url')
        title = song_data.get('title', 'Unknown')
        error_str = str(queue._last_playback_error or 'Unknown error')

        # Notify user on first failure
        if retry_count == 0:
            try:
                await interaction.channel.send(
                    embed=self.make_embed(
                        title="⚠️ Playback interrupted",
                        description=f"Stream for **{title}** failed (`{error_str[:80]}`).\nRe-attempting...",
                        color=0xe67e22
                    ),
                    delete_after=10
                )
            except Exception:
                pass

        if retry_count >= MAX_RETRIES:
            logger.warning(f"Max retries ({MAX_RETRIES}) reached for '{title}', skipping")
            try:
                await interaction.channel.send(
                    embed=self.make_embed(
                        title="❌ Playback failed",
                        description=f"Could not play **{title}** after {MAX_RETRIES} retries.\nSkipping to next song.",
                        color=0xe74c3c
                    ),
                    delete_after=15
                )
            except Exception:
                pass
            queue.playing = False
            await self.play_next(guild, voice_client, interaction)
            return

        try:
            fresh_info = await song_loader.extract_info_async(webpage_url)
            if not fresh_info or "url" not in fresh_info:
                logger.warning(f"Retry {retry_count + 1}: failed to get fresh URL for {webpage_url}")
                await self._retry_failed_song(guild, voice_client, interaction, song_data, retry_count + 1)
                return

            stream_url = fresh_info["url"]

            if song_data.pop('_needs_full_extract', None):
                song_data['title'] = fresh_info.get("title", "Unknown title")
                song_data['thumbnail'] = fresh_info.get("thumbnail")
                song_data['duration'] = fresh_info.get("duration", 0)
                song_data['author'] = fresh_info.get("uploader") or fresh_info.get("creator") or fresh_info.get("artist") or "Unknown"
                song_data['likes'] = fresh_info.get("like_count") or 0
                song_data['views'] = fresh_info.get("view_count") or 0
                song_data['upload_date'] = fresh_info.get("upload_date")
                song_data['source'] = detect_source_from_entry(fresh_info)

            audio_source = await song_loader.preload_audio_source(stream_url)
        except Exception as e:
            logger.error(f"Retry {retry_count + 1} extract error: {e}")
            await self._retry_failed_song(guild, voice_client, interaction, song_data, retry_count + 1)
            return

        volume = guild_volumes.get(guild.id, 1.0)
        audio_source = discord.PCMVolumeTransformer(audio_source, volume=volume)

        def after_retry(e):
            if e:
                logger.error(f"Retry {retry_count + 1} playback error: {e}")
                queue._last_playback_error = e
                asyncio.run_coroutine_threadsafe(
                    self._retry_failed_song(guild, voice_client, interaction, song_data, retry_count + 1),
                    self.bot.loop
                )
            else:
                loop_mode = self.guild_loops.get(guild.id, 'off')
                asyncio.run_coroutine_threadsafe(
                    self._handle_song_end(guild, queue, song_data, loop_mode),
                    self.bot.loop
                )
                asyncio.run_coroutine_threadsafe(
                    self.play_next(guild, voice_client, interaction),
                    self.bot.loop
                )

        queue.playing = True
        try:
            voice_client.play(audio_source, after=after_retry)
            logger.info(f"Retry {retry_count + 1} started for '{title}'")
        except Exception as e:
            logger.error(f"Retry {retry_count + 1} play error: {e}")
            queue.playing = False
            await self._retry_failed_song(guild, voice_client, interaction, song_data, retry_count + 1)

    async def _handle_song_end(self, guild, queue, song_data, loop_mode):
        async with queue.lock:
            if loop_mode == 'song' and song_data:
                queue.queue.appendleft(song_data)
            elif loop_mode == 'queue' and song_data:
                queue._loop_played.append(song_data)
                if queue.is_empty() and queue._loop_played:
                    for s in queue._loop_played:
                        queue.add(s)
                    queue._loop_played.clear()
            queue.playing = False

    async def _get_music_channel(self, interaction):
        try:
            ch = getattr(interaction, "channel", None)
            if ch:
                return ch
        except Exception:
            pass
        return await self.bot.fetch_channel(I_CHANNEL)

    async def play_next(self, guild, voice_client, interaction):
        if guild.id not in guild_queues:
            return

        lock = self._play_next_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            music_channel = await self._get_music_channel(interaction)
            queue = guild_queues.get(guild.id)
            if not queue:
                return

            async with queue.lock:
                if voice_client.is_playing():
                    return

                next_song_data = queue.get_next()

            def after_song(e):
                if e:
                    err_str = str(e).lower()
                    logger.error(f"Playback error: {e}")
                    if any(code in err_str for code in ('403', 'forbidden', '401', 'unauthorized', '410', 'gone')):
                        queue._last_playback_error = e
                        asyncio.run_coroutine_threadsafe(
                            self._retry_failed_song(guild, voice_client, interaction, next_song_data, retry_count=0),
                            self.bot.loop
                        )
                        return
                loop_mode = self.guild_loops.get(guild.id, 'off')
                asyncio.run_coroutine_threadsafe(
                    self._handle_song_end(guild, queue, next_song_data, loop_mode),
                    self.bot.loop
                )
                pn = self.play_next(guild, voice_client, interaction)
                asyncio.run_coroutine_threadsafe(pn, self.bot.loop)

            if next_song_data:
                try:
                    logger.info(f"Playing: {next_song_data.get('title', 'Unknown')} (duration: {next_song_data.get('duration', 0)}, views: {next_song_data.get('views', 0)}, likes: {next_song_data.get('likes', 0)})")
                    logger.info(f"Song URL: {next_song_data.get('song_url', 'Unknown')}")
                    
                    webpage_url = next_song_data['song_url']

                    # Guard: rohe Stream-URL (videoplayback) ist unbrauchbar
                    if 'googlevideo.com' in webpage_url or 'videoplayback' in webpage_url:
                        rescued = (
                            next_song_data.get('entry_data', {}).get('webpage_url')
                            or next_song_data.get('entry_data', {}).get('original_url')
                        )
                        if rescued:
                            logger.warning(f"Stream URL in song_url, rescued webpage_url: {rescued[:60]}")
                            next_song_data['song_url'] = rescued
                            webpage_url = rescued
                        else:
                            logger.warning(f"Stale stream URL, no rescue possible — skipping: {webpage_url[:80]}")
                            queue.playing = False
                            await self.play_next(guild, voice_client, interaction)
                            return

                    preloaded = next_song_data.get('_preloaded_url')
                    preload_age = time.time() - next_song_data.get('_preload_time', 0)

                    if preloaded and preload_age < 300:
                        stream_url = preloaded
                        next_song_data.pop('_needs_full_extract', None)
                    else:
                        fresh_info = await song_loader.extract_info_async(webpage_url)

                        if not fresh_info or "url" not in fresh_info:
                            logger.warning(f"Failed to get fresh stream URL for {webpage_url}")
                            try:
                                await music_channel.send(
                                    embed=self.make_embed(
                                        title="⚠️ Stream unavailable",
                                        description=f"Could not load **{next_song_data.get('title', 'Unknown')}**.\nSkipping...",
                                        color=0xe67e22
                                    ),
                                    delete_after=10
                                )
                            except Exception:
                                pass
                            queue.playing = False
                            await self.play_next(guild, voice_client, interaction)
                            return

                        stream_url = fresh_info["url"]

                        if next_song_data.pop('_needs_full_extract', None):
                            next_song_data['title'] = fresh_info.get("title", "Unknown title")
                            next_song_data['thumbnail'] = fresh_info.get("thumbnail")
                            next_song_data['duration'] = fresh_info.get("duration", 0)
                            next_song_data['author'] = fresh_info.get("uploader") or fresh_info.get("creator") or fresh_info.get("artist") or "Unknown"
                            next_song_data['likes'] = fresh_info.get("like_count") or 0
                            next_song_data['views'] = fresh_info.get("view_count") or 0
                            next_song_data['upload_date'] = fresh_info.get("upload_date")
                            next_song_data['source'] = detect_source_from_entry(fresh_info)

                    audio_source = await song_loader.preload_audio_source(stream_url)
                    
                except Exception as e:
                    err = str(e)[:500]
                    logger.error(f"Error extracting stream: {err}")
                    queue.playing = False
                    try:
                        await music_channel.send(
                            embed=self.make_embed(
                                title="❌ Stream extraction failed",
                                description=f"Could not load **{next_song_data.get('title', 'Unknown')}**.\n```{err[:200]}```",
                                color=0xe74c3c
                            ),
                            delete_after=12
                        )
                    except Exception:
                        pass
                    await self.play_next(guild, voice_client, interaction)
                    return

                queue.playing = True
                self.songs_played += 1

                volume = guild_volumes.get(guild.id, 1.0)
                audio_source = discord.PCMVolumeTransformer(audio_source, volume=volume)

                try:
                    voice_client.play(audio_source, after=after_song)
                except Exception as e:
                    logger.error(f"Error starting playback: {e}")
                    queue.playing = False
                    try:
                        await music_channel.send(
                            embed=self.make_embed(
                                title="❌ Playback start failed",
                                description=f"Could not start playing **{next_song_data.get('title', 'Unknown')}**.\n```{str(e)[:200]}```",
                                color=0xe74c3c
                            ),
                            delete_after=12
                        )
                    except Exception:
                        pass
                    await self.play_next(guild, voice_client, interaction)
                    return

                self.create_background_task(self._preload_next(guild.id))

                self.currently_playing[guild.id] = {
                    'song_data': next_song_data,
                    'started_at': time.time(),
                    'elapsed': 0,
                    'is_paused': False,
                }

                metadata = (
                    next_song_data['title'],
                    next_song_data['thumbnail'],
                    None,
                    next_song_data['duration'],
                    next_song_data['author'],
                    next_song_data['song_url'],
                    next_song_data['likes'],
                    next_song_data['views'],
                    next_song_data['upload_date'],
                    next_song_data.get('source', {"label": "Unknown", "icon": "🔗", "extractor": None}),
                    next_song_data.get('requested_by_name'),
                    next_song_data.get('requested_by_id'),
                )
                
                embed = self.create_now_playing_embed(metadata, interaction)
                try:
                    old_msg = self._now_playing_messages.get(guild.id)
                    if old_msg:
                        try:
                            await old_msg.delete()
                        except Exception:
                            pass

                    np_view = NowPlayingView(self, next_song_data['title'], next_song_data['author'])
                    msg = await music_channel.send(embed=embed, view=np_view)
                    self._now_playing_messages[guild.id] = msg
                    self._schedule_progress(guild.id, msg, embed, next_song_data['duration'])
                except Exception as e:
                    logger.error(f"Error sending now playing message: {e}")

                await self._set_song_activity(next_song_data['title'], next_song_data['author'])
            elif AUTO_PLAY_ENABLED:
                logger.info("autoplaying")

                current = self.currently_playing.get(guild.id)
                song_link = current.get('song_data', {}).get('song_url') if current else None

                if not song_link:
                    logger.warning("No song link found for autoplay")
                    queue.playing = False
                    return

                source_type = detect_source(song_link)
                is_youtube = source_type["extractor"] == "youtube"

                if not is_youtube:
                    logger.warning(f"Autoplay: non-YouTube source ({source_type['label']}) — autoplay only supported for YouTube")
                    queue.playing = False
                    return

                try:
                    video_id_match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", song_link)
                    if not video_id_match:
                        logger.warning(f"Could not extract video ID from link: {song_link}")
                        queue.playing = False
                        return

                    video_id = video_id_match.group(1)

                    def _fetch_related():
                        return self._ytmusic.get_song_related(video_id)

                    related_songs = await asyncio.get_running_loop().run_in_executor(
                        song_loader.executor, _fetch_related
                    )

                    if not related_songs:
                        logger.info("No related songs found")
                        queue.playing = False
                        return

                    suggestion = related_songs[0]["videoid"]
                    webpage_url = f"https://www.youtube.com/watch?v={suggestion}"
                except Exception as e:
                    logger.error(f"Autoplay suggestion error: {e}")
                    queue.playing = False
                    return

                try:
                    fresh_info = await song_loader.extract_info_async(webpage_url)

                    if not fresh_info or "url" not in fresh_info:
                        logger.warning(f"Failed to get fresh stream URL for {webpage_url}")
                        try:
                            await music_channel.send(
                                embed=self.make_embed(
                                    title="⚠️ Autoplay stream unavailable",
                                    description="Could not load the autoplay suggestion.\nSkipping...",
                                    color=0xe67e22
                                ),
                                delete_after=10
                            )
                        except Exception:
                            pass
                        queue.playing = False
                        await self.play_next(guild, voice_client, interaction)
                        return

                    stream_url = fresh_info["url"]
                    audio_source = await song_loader.preload_audio_source(stream_url)

                except Exception as e:
                    err = str(e)[:500]
                    logger.error(f"Autoplay extract error: {err}")
                    queue.playing = False
                    try:
                        await music_channel.send(
                            embed=self.make_embed(
                                title="⚠️ Autoplay error",
                                description=f"Could not extract autoplay audio.\n```{err[:200]}```",
                                color=0xe74c3c
                            ),
                            delete_after=10
                        )
                    except Exception:
                        pass
                    await self.play_next(guild, voice_client, interaction)
                    return

                queue.playing = True
                self.songs_played += 1

                volume = guild_volumes.get(guild.id, 1.0)
                audio_source = discord.PCMVolumeTransformer(audio_source, volume=volume)

                try:
                    voice_client.play(audio_source, after=after_song)
                except Exception as e:
                    logger.error(f"Error starting autoplay playback: {e}")
                    queue.playing = False
                    try:
                        await music_channel.send(
                            embed=self.make_embed(
                                title="❌ Autoplay failed",
                                description=f"Could not start autoplay.\n```{str(e)[:200]}```",
                                color=0xe74c3c
                            ),
                            delete_after=10
                        )
                    except Exception:
                        pass
                    return

                self.create_background_task(self._preload_next(guild.id))

                processed = await self.process_single_entry(fresh_info, requester=interaction.user)
                if processed:
                    self.currently_playing[guild.id] = {
                        'song_data': processed,
                        'started_at': time.time(),
                        'elapsed': 0,
                        'is_paused': False,
                    }
                    embed = self.create_now_playing_embed((
                        processed['title'],
                        processed['thumbnail'],
                        None,
                        processed['duration'],
                        processed['author'],
                        processed['song_url'],
                        processed['likes'],
                        processed['views'],
                        processed['upload_date'],
                        processed['source'],
                        processed.get('requested_by_name'),
                        processed.get('requested_by_id'),
                    ), interaction)
                    try:
                        old_msg = self._now_playing_messages.get(guild.id)
                        if old_msg:
                            try:
                                await old_msg.delete()
                            except Exception:
                                pass

                        np_view = NowPlayingView(self, processed['title'], processed['author'])
                        msg = await music_channel.send(embed=embed, view=np_view)
                        self._now_playing_messages[guild.id] = msg
                        self._schedule_progress(guild.id, msg, embed, processed['duration'])
                    except Exception as e:
                        logger.error(f"Error sending now playing message: {e}")

                    await self._set_song_activity(processed['title'], processed['author'])

            else:
                logger.info("queue stopped")
                queue.playing = False
                self.currently_playing.pop(guild.id, None)
                await self._reset_activity()

    def create_now_playing_embed(self, metadata, interaction):
        title, thumbnail, _, duration, author, song_url, likes, views, upload_date, source, req_name, req_id = metadata

        def format_time(seconds):
            m, s = divmod(int(seconds), 60)
            return f"{m:02}:{s:02}"

        fields = [
            ("Artist", f"{author}", True),
            ("Duration", f"{format_time(duration)}", True),
        ]

        source_label = source.get("label", "Unknown")
        source_icon = source.get("icon", "🔗")
        fields.append(("Platform", f"{source_icon} {source_label}", True))

        if likes:
            fields.append(("Likes", f"👍 {likes}", True))
        if views:
            fields.append(("Views", f"👁️ {views}", True))

        embed = self.make_embed(
            title="Now playing",
            description=f"{source_icon} **{title}**",
            color=0x5865F2,
            thumbnail=thumbnail,
            author_name=f"Requested by {req_name or interaction.user.display_name}",
            author_icon=safe_avatar(interaction.user),
            footer="Use /skip to go to the next song",
            footer_icon=safe_avatar(self.bot.user),
            fields=[(n, f"```\n{v}\n```", True) for n, v, _ in fields]
        )
        return embed

    def _resolve_source_for_url(self, url: str, original_source_name: Optional[str] = None) -> dict:
        if original_source_name:
            for key, s in MUSIC_SOURCES.items():
                if s["label"].lower() == original_source_name.lower():
                    return s
            return {"label": original_source_name, "icon": "🔗", "extractor": None}
        return detect_source(url)

    async def _set_song_activity(self, title: str, author: str):
        try:
            await self.bot.change_presence(
                activity=discord.Activity(
                    name=f"{title} - {author}",
                    type=discord.ActivityType.listening
                )
            )
        except Exception:
            pass

    async def _reset_activity(self):
        try:
            await self.bot.change_presence(
                activity=discord.Activity(
                    name="/github • /play",
                    type=discord.ActivityType.competing
                )
            )
        except Exception:
            pass

    async def _ensure_voice(self, interaction, queue):
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            if not interaction.user.voice:
                return None
            channel = interaction.user.voice.channel
            await channel.connect(self_deaf=True)
            voice_client = interaction.guild.voice_client
            if SET_VC_STATUS_TO_MUSIC_PLAYING:
                p = queue.peek()
                current_song = p['title'] if p else "Music"
                try:
                    await voice_client.channel.edit(status=f"Listening to: {current_song}")
                except Exception:
                    pass
        return voice_client

    async def _maybe_play(self, voice_client, queue, interaction, check_empty=True):
        if not voice_client:
            return
        if interaction.user.voice and interaction.user.voice.channel != voice_client.channel:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Wrong voice channel",
                    description="You must be in the same voice channel as the bot.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return
        if not voice_client.is_playing() and not voice_client.is_paused():
            if check_empty and queue.is_empty():
                return
            await self.play_next(guild=interaction.guild, voice_client=voice_client, interaction=interaction)

    async def _search_and_play(self, interaction: discord.Interaction, search_term: str, color: int = 0x3498db, loading_message: discord.WebhookMessage = None):
        """Shared helper: search a song, add to queue, and start playback."""
        if not interaction.user.voice:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Voice channel required",
                    description="Join a voice channel and try again.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        search_query = f"ytsearch:{search_term}"

        try:
            info = await song_loader.extract_info_async(search_query)
        except Exception as e:
            logger.error(f"Failed to load song '{search_term}': {e}")
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Error",
                    description=f"Failed to load song.\n\n{e}",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if interaction.guild.id not in guild_queues:
            guild_queues[interaction.guild.id] = OptimizedQueue()

        queue = guild_queues[interaction.guild.id]

        entry = info["entries"][0] if "entries" in info and info["entries"] else info

        processed_song = await self.process_single_entry(entry, requester=interaction.user)
        if processed_song:
            queue.add(processed_song)
            title = processed_song['title']
            thumbnail = processed_song['thumbnail']

            success_embed = self.make_embed(
                title="Added to queue",
                description=title,
                color=color,
                thumbnail=thumbnail,
                fields=[
                    ("Position", f"```\n#{len(queue.queue)}\n```", True)
                ]
            )

            if loading_message:
                await loading_message.edit(embed=success_embed)
            else:
                await interaction.channel.send(embed=success_embed)

        voice_client = await self._ensure_voice(interaction, queue)
        await self._maybe_play(voice_client, queue, interaction)

    async def update_progress(self, message, embed, duration):
        try:
            await asyncio.sleep(max(0, int(duration)))
            embed.color = 0x95a5a6
            embed.title = "Last Played"
            embed.set_footer(text="Playback finished", icon_url=safe_avatar(self.bot.user))
            await message.edit(embed=embed)
        except (discord.HTTPException, asyncio.CancelledError):
            pass

    def _schedule_progress(self, guild_id, msg, embed, duration):
        old = self._progress_tasks.get(guild_id)
        if old and not old.done():
            old.cancel()
        task = self.create_background_task(self.update_progress(msg, embed, duration))
        self._progress_tasks[guild_id] = task

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        return f"{m:02}:{s:02}"

    async def process_single_entry(self, entry: dict, requester: Optional[discord.abc.User] = None, original_source: Optional[str] = None, flat: bool = False):
        try:
            if not entry or ("url" not in entry and "webpage_url" not in entry):
                logger.error(f"Error processing entry: Missing 'url' or 'webpage_url' key")
                return None

            song_url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url") or "Unknown URL"
            source = detect_source_from_entry(entry)
            if original_source:
                override = self._resolve_source_for_url("", original_source)
                if override:
                    source = override

            if flat:
                return {
                    'entry_data': entry,
                    'title': entry.get("title", "Unknown title"),
                    'song_url': song_url,
                    'source': source,
                    'original_source': original_source or source.get("label"),
                    'requested_by_id': requester.id if requester else 0,
                    'requested_by_name': requester.display_name if requester else "Unknown",
                    '_needs_full_extract': True,
                }

            return {
                'entry_data': entry,
                'title': entry.get("title", "Unknown title"),
                'thumbnail': entry.get("thumbnail"),
                'duration': entry.get("duration", 0),
                'author': entry.get("uploader") or entry.get("creator") or entry.get("artist") or "Unknown",
                'song_url': song_url,
                'likes': entry.get("like_count") or 0,
                'views': entry.get("view_count") or 0,
                'upload_date': entry.get("upload_date"),
                'source': source,
                'original_source': original_source or source.get("label"),
                'requested_by_id': requester.id if requester else 0,
                'requested_by_name': requester.display_name if requester else "Unknown",
            }

        except Exception as e:
            logger.error(f"Error processing entry: {e}")
            return None

    async def _load_and_process_url(self, url: str, requester: Optional[discord.abc.User] = None) -> Optional[dict]:
        try:
            if needs_resolution(url):
                url = await resolve_to_direct_url(url) or url
            info = await song_loader.extract_info_async(url)
            if not info:
                return None
            song_data = info["entries"][0] if "entries" in info and info.get("entries") else info
            return await self.process_single_entry(song_data, requester=requester)
        except Exception as e:
            logger.error(f"Failed to load track {url}: {e}")
            return None

    async def _quick_add_song(self, url: str, requester: Optional[discord.abc.User] = None) -> Optional[dict]:
        try:
            if needs_resolution(url):
                url = await resolve_to_direct_url(url) or url
            if not url:
                return None

            # Kein yt-dlp-Call — URL direkt als Stub in die Queue.
            # play_next / _preload_next extrahieren Metadaten kurz vor dem Abspielen.
            source = detect_source(url)
            return {
                'title': 'Loading...',
                'song_url': url,
                'thumbnail': None,
                'duration': 0,
                'author': 'Unknown',
                'likes': 0,
                'views': 0,
                'upload_date': None,
                'source': source,
                'original_source': source.get("label"),
                'requested_by_id': requester.id if requester else 0,
                'requested_by_name': requester.display_name if requester else "Unknown",
                '_needs_full_extract': True,
            }
        except Exception as e:
            logger.error(f"Failed to quick-add track {url}: {e}")
            return None

    async def _preload_next(self, guild_id: int):
        queue = guild_queues.get(guild_id)
        if not queue:
            return
        next_song = queue.peek()
        if not next_song or next_song.get('_preloaded_url'):
            return
        try:
            info = await song_loader.extract_info_async(next_song['song_url'])
            if info and 'url' in info:
                next_song['_preloaded_url'] = info['url']
                next_song['_preload_time'] = time.time()
                if next_song.pop('_needs_full_extract', None):
                    next_song['title'] = info.get("title", "Unknown title")
                    next_song['thumbnail'] = info.get("thumbnail")
                    next_song['duration'] = info.get("duration", 0)
                    next_song['author'] = info.get("uploader") or info.get("creator") or info.get("artist") or "Unknown"
                    next_song['likes'] = info.get("like_count") or 0
                    next_song['views'] = info.get("view_count") or 0
                    next_song['upload_date'] = info.get("upload_date")
                    next_song['source'] = detect_source_from_entry(info)
        except Exception:
            pass

    async def process_song_entries(self, entries: List[dict], guild_id: int, requester: Optional[discord.abc.User] = None, original_source: Optional[str] = None, flat: bool = False):
        if guild_id not in guild_queues:
            guild_queues[guild_id] = OptimizedQueue()

        queue = guild_queues[guild_id]
        processed_songs = []
        total = len(entries)

        batch_size = 5
        for i in range(0, len(entries), batch_size):
            batch = entries[i:i + batch_size]

            tasks = []
            for entry in batch:
                if entry:
                    tasks.append(self.process_single_entry(entry, requester, original_source, flat=flat))

            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception) or not result:
                        continue
                    processed_songs.append(result)
                    queue.add(result)

        return processed_songs, total - len(processed_songs)

    @app_commands.command(name="chart", description="Plays a random song from the YouTube Music charts")
    async def play_chart(self, interaction: discord.Interaction):
        if await self.check_timeout_decorator(interaction):
            return
        else:
            await interaction.response.defer()

        loading_embed = self.make_embed(
            title="Loading chart",
            description="Fetching popular songs...",
            color=0x3498db,
        )

        loading_message = await interaction.followup.send(embed=loading_embed)

        try:
            chart_urls = [
                "https://music.youtube.com/playlist?list=RDCLAK5uy_kmPRjHDECIcuVwnKsx5w4UBCp9jSEMzM",
                "https://music.youtube.com/playlist?list=RDCLAK5uy_k8jhb5wP3rUqLOWFzVQNE_YdIcF7O4BN",
                "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
            ]

            playlist_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'playlist_items': '1-20',
            }

            trending_songs = []

            for chart_url in chart_urls:
                try:
                    def extract_playlist_info():
                        with yt_dlp.YoutubeDL(playlist_opts) as ydl:
                            return ydl.extract_info(chart_url, download=False)

                    chart_info = await asyncio.get_running_loop().run_in_executor(
                        song_loader.executor, extract_playlist_info
                    )

                    if "entries" in chart_info and chart_info["entries"]:
                        for entry in chart_info["entries"][:15]:
                            if entry and entry.get("title"):
                                title = entry["title"]
                                uploader = entry.get("uploader", "")
                                if uploader and uploader.lower() not in title.lower():
                                    song_query = f"{title} {uploader}"
                                else:
                                    song_query = title
                                trending_songs.append(song_query)

                        if trending_songs:
                            break

                except Exception as e:
                    logger.error(f"Fehler beim Laden der Playlist {chart_url}: {e}")
                    continue

            if not trending_songs:
                try:
                    search_queries = [
                        f"ytsearch5:music charts {datetime.now().year}",
                        "ytsearch5:trending music now",
                        f"ytsearch5:top songs {datetime.now().year}"
                    ]

                    for search_query in search_queries:
                        try:
                            search_results = await song_loader.extract_info_async(search_query)
                            if "entries" in search_results:
                                for entry in search_results["entries"][:5]:
                                    if entry and entry.get("title"):
                                        title = entry["title"]
                                        uploader = entry.get("uploader", "")
                                        if uploader and uploader.lower() not in title.lower():
                                            song_query = f"{title} {uploader}"
                                        else:
                                            song_query = title
                                        trending_songs.append(song_query)

                                if trending_songs:
                                    break
                        except Exception as e:
                            logger.error(f"Fehler bei der Suche {search_query}: {e}")
                            continue

                except Exception as e:
                    logger.error(f"Fehler bei der Fallback-Suche: {e}")

            if not trending_songs:
                trending_songs = [
                    "Flowers Miley Cyrus",
                    "As It Was Harry Styles",
                    "Bad Habit Steve Lacy",
                    "About Damn Time Lizzo",
                    "Heat Waves Glass Animals",
                    "Stay The Kid LAROI Justin Bieber",
                    "Ghost Justin Bieber",
                    "Industry Baby Lil Nas X",
                    "Good 4 U Olivia Rodrigo",
                    "Levitating Dua Lipa"
                ]

            random_chart_song = random.choice(trending_songs)

            loading_embed = self.make_embed(
                title="Loading chart",
                description=f"Selected: {random_chart_song}\nPreparing...",
                color=0x3498db,
            )
            await loading_message.edit(embed=loading_embed)

        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Charts: {e}")
            fallback_songs = [
                "Flowers Miley Cyrus",
                "As It Was Harry Styles",
                "Bad Habit Steve Lacy",
                "About Damn Time Lizzo",
                "Heat Waves Glass Animals"
            ]
            random_chart_song = random.choice(fallback_songs)

            loading_embed = self.make_embed(
                title="Loading chart (fallback)",
                description=f"Selected: {random_chart_song}\nPreparing...",
                color=0xe67e22,
            )
            await loading_message.edit(embed=loading_embed)

        await self._search_and_play(interaction, random_chart_song, color=0x2ecc71, loading_message=loading_message)

    RANDOM_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLla6SkKQuad-Kizph600BnYVuU0r9j-Da"

    async def _fetch_playlist(self):
        loop = asyncio.get_running_loop()
        def run():
            with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True}) as ydl:
                return ydl.extract_info(self.RANDOM_PLAYLIST_URL, download=False)
        return await loop.run_in_executor(song_loader.executor, run)

    async def _ensure_playlist_cache(self):
        now = time.time()
        if not hasattr(self, '_playlist_cache') or not self._playlist_cache or now - self._playlist_cache_time > self._playlist_cache_ttl:
            info = await self._fetch_playlist()
            self._playlist_cache = [e for e in info.get("entries", []) if e.get("title") and e.get("url")]
            self._playlist_cache_time = now

    async def inspire_me(self, interaction: discord.Interaction):
        if await self.check_timeout_decorator(interaction):
            return
        await interaction.response.defer()

        if not interaction.user.voice:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Voice channel required",
                    description="Join a voice channel and try again.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        try:
            await self._ensure_playlist_cache()

            if not self._playlist_cache:
                raise Exception("No songs found in playlist")

            entry = random.choice(self._playlist_cache)
            song_url = entry["url"]

            info = await song_loader.extract_info_async(song_url)
        except Exception as e:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Error",
                    description=f"Failed to load song.\n\n{e}",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if interaction.guild.id not in guild_queues:
            guild_queues[interaction.guild.id] = OptimizedQueue()

        queue = guild_queues[interaction.guild.id]

        song_entry = info["entries"][0] if "entries" in info and info["entries"] else info

        processed_song = await self.process_single_entry(song_entry, requester=interaction.user)
        if not processed_song:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Error",
                    description="Could not process the selected song.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        queue.add(processed_song)

        voice_client = await self._ensure_voice(interaction, queue)
        if not voice_client:
            return
        await self._maybe_play(voice_client, queue, interaction)

    async def inspire_me_playlist(self, interaction: discord.Interaction):
        if await self.check_timeout_decorator(interaction):
            return
        await interaction.response.defer()

        if not interaction.user.voice:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Voice channel required",
                    description="Join a voice channel and try again.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        try:
            await self._ensure_playlist_cache()

            if not self._playlist_cache:
                raise Exception("No songs found in playlist")

            shuffled = random.sample(self._playlist_cache, len(self._playlist_cache))

            loading = await interaction.followup.send(
                embed=self.make_embed(
                    title="Shuffling playlist",
                    description=f"Preparing {len(shuffled)} songs...",
                    color=0xf39c12
                )
            )

            if interaction.guild.id not in guild_queues:
                guild_queues[interaction.guild.id] = OptimizedQueue()
            queue = guild_queues[interaction.guild.id]

            async def _extract_flat(entry):
                try:
                    info = await song_loader.extract_info_flat_async(entry["url"])
                    if info and not ("entries" in info and not [e for e in info["entries"] if e]):
                        return info["entries"][0] if "entries" in info else info
                except Exception:
                    pass
                return None

            tasks = [_extract_flat(e) for e in shuffled]
            flat_entries = [e for e in await asyncio.gather(*tasks) if e]

            if not flat_entries:
                await loading.edit(embed=self.make_embed(
                    title="Error",
                    description="Could not load any songs from playlist.",
                    color=0xe74c3c
                ))
                return

            processed, skipped = await self.process_song_entries(flat_entries, interaction.guild.id, requester=interaction.user, flat=True)

            titles_list = "\n".join([f"- {s['title']}" for s in processed[:10]])
            if len(processed) > 10:
                titles_list += f"\n\n...and {len(processed) - 10} more."

            desc = f"{len(processed)} songs added to queue."
            if skipped > 0:
                desc += f"\n⚠️ {skipped} song{'s' if skipped != 1 else ''} skipped (unavailable)."

            await loading.edit(embed=self.make_embed(
                title="🎲 Playlist Shuffle",
                description=f"{desc}\n\n{titles_list}",
                color=0x2ecc71,
                fields=[
                    ("Position", f"```\n#{len(queue.queue) - len(processed) + 1}\n```", True),
                ]
            ))

        except Exception as e:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Error",
                    description=f"Failed to load playlist.\n\n{e}",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        voice_client = await self._ensure_voice(interaction, queue)
        if not voice_client:
            return
        await self._maybe_play(voice_client, queue, interaction)

    async def mostplayed_callback(self, interaction: discord.Interaction, song: str):
        await interaction.response.defer()

        loading_embed = self.make_embed(
            title="Loading",
            description=f"Selected: {song}\nPreparing...",
            color=0x3498db
        )

        loading_message = await interaction.followup.send(embed=loading_embed)

        await self._search_and_play(interaction, song, color=0xf39c12, loading_message=loading_message)

    @app_commands.command(name="play", description="Plays music from YouTube, Spotify, SoundCloud, Deezer, Tidal and more")
    @app_commands.describe(song="URL or search term", source="Music source (default: auto)")
    @app_commands.choices(source=SOURCE_CHOICES)
    async def play(self, interaction: discord.Interaction, song: str, source: str = "auto"):
        if await self.check_timeout_decorator(interaction):
            return
        else:
            try:
                await interaction.response.defer()
            except Exception:
                pass

        if interaction.guild.id not in guild_queues:
            guild_queues[interaction.guild.id] = OptimizedQueue()
        queue = guild_queues[interaction.guild.id]
        voice_client = interaction.guild.voice_client

        if voice_client and voice_client.channel and interaction.user.voice and interaction.user.voice.channel != voice_client.channel:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Wrong voice channel",
                    description="You must be in the same voice channel as the bot.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if not interaction.user.voice:
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Voice channel required",
                    description="Join a voice channel and try again.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        loading_source = detect_source(song)["label"] if song.startswith("http") else (source.capitalize() if source != "auto" else "YouTube")
        loading_embed = self.make_embed(
            title="Loading",
            description=f"Searching {loading_source}: {song}",
            color=0x3498db
        )
        loading_message = await interaction.followup.send(embed=loading_embed)

        is_url = song.startswith("http")
        if is_url:
            search_query = song
        elif source != "auto":
            prefix = SEARCH_PREFIXES.get(source)
            if prefix:
                search_query = f"{prefix}:{song}"
            else:
                await interaction.followup.send(
                    embed=self.make_embed(
                        title="Unsupported source",
                        description=f"**{source}** does not support text search. Please provide a direct URL or use a different source.",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
                try:
                    await loading_message.delete()
                except Exception:
                    pass
                return
        else:
            search_query = f"ytsearch:{song}"

        info = None
        err_msg = None
        source_hint = detect_source(song)["label"] if is_url else (source.capitalize() if source != "auto" else "YouTube")
        is_flat_playlist = False

        try:
            if is_url and bool(YT_PLAYLIST_RE.search(song)):
                info = await song_loader.extract_info_flat_async(search_query)
                if info and "entries" in info and [e for e in info["entries"] if e]:
                    is_flat_playlist = True
                elif info and not ("entries" in info):
                    pass  # Single result — use as-is, play_next fills metadata
                else:
                    info = None
            else:
                info = await song_loader.extract_info_async(search_query)
        except Exception as e:
            err_msg = str(e)[:1500]

        original_source_name = get_source_name(song) if is_url else None

        # Try resolver fallback when yt-dlp returns no results
        if is_url and (not info or ("entries" in info and not [e for e in info["entries"] if e])):
            if is_playlist(song):
                resolved_tracks = await resolve_playlist(song)
                if resolved_tracks:
                    async def _extract(track):
                        try:
                            ti = await song_loader.extract_info_async(track["query"])
                            if ti and not ("entries" in ti and not [e for e in ti["entries"] if e]):
                                return ti["entries"][0] if "entries" in ti else ti
                        except Exception:
                            pass
                        return None
                    tasks = [_extract(t) for t in resolved_tracks]
                    entries = [e for e in await asyncio.gather(*tasks) if e]
                    if entries:
                        info = {"entries": entries}
                        source_hint = original_source_name or source_hint
                    else:
                        info = None
                if not info:
                    await interaction.followup.send(
                        embed=self.make_embed(
                            title="Unsupported playlist",
                            description=f"**{source_hint}** playlist or album could not be resolved.",
                            color=0xe74c3c
                        ),
                        ephemeral=True
                    )
                    return

            if needs_resolution(song):
                resolved = await resolve_to_search_query(song)
                if resolved:
                    try:
                        info = await song_loader.extract_info_async(resolved["query"])
                        if info and not ("entries" in info and not [e for e in info["entries"] if e]):
                            source_hint = f"{source_hint} → YouTube"
                        else:
                            info = None
                    except Exception:
                        info = None
                else:
                    info = None
                if not info:
                    direct_url = await resolve_to_direct_url(song)
                    if direct_url:
                        try:
                            info = await song_loader.extract_info_async(direct_url)
                            if info and not ("entries" in info and not [e for e in info["entries"] if e]):
                                source_hint = f"{source_hint} → YouTube Music"
                            else:
                                info = None
                        except Exception:
                            info = None
            if not info:
                try:
                    await loading_message.delete()
                except Exception:
                    pass
                await interaction.followup.send(
                    embed=self.make_embed(
                        title="Extraction failed",
                        description=f"**{source_hint}** could not be processed.\nCould not find this track on YouTube either.\n\n```{err_msg or 'No data returned'}```",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
                return

        if not info or ("entries" in info and not [e for e in info["entries"] if e]):
            try:
                await loading_message.delete()
            except Exception:
                pass
            if err_msg:
                await interaction.followup.send(
                    embed=self.make_embed(
                        title="Extraction failed",
                        description=f"**{source_hint}** could not be processed.\n\n```{err_msg}```",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    embed=self.make_embed(
                        title="No results",
                        description="No results found for the given input.\nTry a different search term or check the URL.",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
            return

        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            await loading_message.edit(embed=self.make_embed(
                title="Processing playlist",
                description=f"Found {len(entries)} items.\nAdding to queue...",
                color=0xf39c12
            ))

            processed_songs, skipped = await self.process_song_entries(entries, interaction.guild.id, requester=interaction.user, original_source=original_source_name, flat=is_flat_playlist)

            titles_list = "\n".join([f"- {s['title']}" for s in processed_songs[:10]])
            if len(processed_songs) > 10:
                titles_list += f"\n\n...and {len(processed_songs) - 10} more."

            initial_len = max(0, len(queue.queue) - len(processed_songs))
            wait_seconds = sum(song.get('duration', 0) or 0 for song in list(queue.queue)[:initial_len]) if initial_len > 0 else 0

            first_source = detect_source_from_entry(entries[0]) if entries else detect_source(song)
            if original_source_name:
                override = self._resolve_source_for_url("", original_source_name)
                if override:
                    first_source = override
            source_icon = first_source.get("icon", "🎵")

            desc = f"{len(processed_songs)} songs added to queue."
            if skipped > 0:
                desc += f"\n⚠️ {skipped} song{'s' if skipped != 1 else ''} skipped (unavailable)."

            embed_fields = [
                ("Platform", f"```\n{first_source['label']}\n```", True),
                ("Position", f"```\n#{initial_len + 1}\n```", True),
            ]
            if wait_seconds > 0:
                embed_fields.append(("Estimated time", f"```\n{self.format_time(wait_seconds)}\n```", True))

            await loading_message.edit(embed=self.make_embed(
                title=f"{source_icon} Playlist added",
                description=f"{desc}\n\n{titles_list}",
                color=0x2ecc71,
                thumbnail=(entries[0].get("thumbnail") if entries else None),
                fields=embed_fields,
            ))

        else:
            processed_song = await self.process_single_entry(info, requester=interaction.user, original_source=original_source_name)
            if processed_song:
                queue.add(processed_song)
                source_info = processed_song.get('source') or detect_source_from_entry(info)
                source_icon = source_info.get("icon", "🎵")

                await loading_message.edit(embed=self.make_embed(
                    title=f"{source_icon} Added to queue",
                    description=processed_song['title'],
                    color=0x2ecc71,
                    thumbnail=processed_song['thumbnail'],
                    fields=[
                        ("Platform", f"```\n{source_info['label']}\n```", True),
                        ("Duration", f"```\n{self.format_time(processed_song['duration'])}\n```", True),
                        ("Position", f"```\n#{len(queue.queue)}\n```", True),
                    ]
                ))

        voice_client = await self._ensure_voice(interaction, queue)
        await self._maybe_play(voice_client, queue, interaction, check_empty=False)

    @app_commands.command(name="skip", description="skips the current song")
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if await self.check_timeout_decorator(interaction):
            return

        if not voice_client or (not voice_client.is_playing() and not voice_client.is_paused()):
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Nothing playing",
                    description="Use /play to start music.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if not interaction.user.voice or interaction.user.voice.channel != voice_client.channel:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Wrong voice channel",
                    description="You must be in the same voice channel as the bot.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        queue = guild_queues.get(interaction.guild.id)
        if queue:
            queue.playing = False

        voice_client.stop()
        self.currently_playing.pop(interaction.guild.id, None)

        next_song = queue.queue[0] if queue and queue.queue else None

        if next_song:
            title = next_song['title']
            thumbnail = next_song['thumbnail']

            skip_embed = self.make_embed(
                title="Skipped",
                description=f"Up next: {title}",
                color=0x3498db,
                thumbnail=thumbnail,
                fields=[
                    ("Songs left", f"```\n{len(queue.queue) - 1}\n```", True)
                ]
            )

            await interaction.response.send_message(embed=skip_embed)
        else:
            skip_embed = self.make_embed(
                title="Skipped",
                description="Queue is empty.",
                color=0x95a5a6
            )

            await interaction.response.send_message(embed=skip_embed)

    @app_commands.command(name="queue", description="lists queued songs")
    async def list(self, interaction: discord.Interaction):
        queue = guild_queues.get(interaction.guild.id)
        if await self.check_timeout_decorator(interaction):
            return

        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.channel:
            if not interaction.user.voice or interaction.user.voice.channel != voice_client.channel:
                await interaction.response.send_message(
                    embed=self.make_embed(
                        title="Wrong voice channel",
                        description="You must be in the same voice channel as the bot.",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
                return

        if not queue or not queue.queue:
            empty_embed = self.make_embed(
                title="Queue is empty",
                description="Use /play to add some music.",
                color=0x95a5a6,
                fields=[
                    ("Quick start", "```\n/play <song>\n/chart\n```", False)
                ]
            )
            await interaction.response.send_message(embed=empty_embed)
            return

        items_per_page = 10
        total_pages = max(1, (len(queue.queue) + items_per_page - 1) // items_per_page)
        total_duration = sum(song['duration'] for song in queue.queue)

        queue_snapshot = list(queue.queue)

        def make_queue_embed(page: int = 0):
            start_idx = page * items_per_page
            end_idx = min(start_idx + items_per_page, len(queue_snapshot))
            page_items = queue_snapshot[start_idx:end_idx]

            embed = self.make_embed(
                title=f"Queue ({len(queue_snapshot)} songs)",
                description="Upcoming tracks:",
                color=0x5865F2,
                author_name=interaction.user.display_name,
                author_icon=safe_avatar(interaction.user),
                footer=f"Page {page + 1}/{total_pages} • Total duration: {self.format_time(total_duration)}",
                footer_icon=safe_avatar(self.bot.user),
                thumbnail=safe_avatar(interaction.user)
            )

            wait_time = sum(s['duration'] for s in queue_snapshot[:start_idx])
            for i, song_data in enumerate(page_items):
                idx = start_idx + i + 1
                title = song_data['title']
                duration = song_data['duration']
                embed.add_field(
                    name=f"{idx}. {title}",
                    value=f"```\nDuration: {self.format_time(duration)} • Starts in: {self.format_time(wait_time)}\n```",
                    inline=False
                )
                wait_time += duration

            return embed

        view = QueueView(queue_snapshot, make_queue_embed, items_per_page)
        embed = make_queue_embed(0)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="stop", description="Disconnects the Bot")
    async def leave(self, i: discord.Interaction):
        if await self.check_timeout_decorator(i):
            return
        voice_client = i.guild.voice_client
        if voice_client and voice_client.channel:
            if not i.user.voice or i.user.voice.channel != voice_client.channel:
                await i.response.send_message(
                    embed=self.make_embed(
                        title="Wrong voice channel",
                        description="You must be in the same voice channel as the bot.",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
                return

        queue = guild_queues.get(i.guild.id)

        if queue and queue.queue:
            total_duration = sum(s['duration'] for s in queue.queue)
            cleared_count = len(queue.queue)
            queue.clear()
        else:
            total_duration = 0
            cleared_count = 0

        embed = self.make_embed(
            title="Disconnected",
            description="Left the voice channel.",
            color=0xe74c3c,
            author_name=i.user.display_name,
            author_icon=safe_avatar(i.user),
            footer="See you next time!",
            footer_icon=safe_avatar(self.bot.user),
            thumbnail=safe_avatar(i.user),
            fields=[
                ("Session summary",
                 f"```\nTime left in queue: {self.format_time(total_duration)}\nSongs cleared: {cleared_count}\n```",
                 False)
            ]
        )

        vc = i.guild.voice_client
        if vc:
            try:
                await vc.channel.edit(status=None)
            except Exception:
                pass
            await vc.disconnect()
            self.currently_playing.pop(i.guild.id, None)
            self._now_playing_messages.pop(i.guild.id, None)
            await self._reset_activity()
            await i.response.send_message(embed=embed)
            await self.send_static_message()
        else:
            await i.response.send_message(
                embed=self.make_embed(
                    title="Not connected",
                    description="The bot is not connected to a voice channel.",
                    color=0xe74c3c
                )
            )

    @app_commands.command(name="shuffle", description="Shuffles the queue")
    async def shuffle(self, interaction: discord.Interaction):
        if await self.check_timeout_decorator(interaction):
            return

        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.channel:
            if not interaction.user.voice or interaction.user.voice.channel != voice_client.channel:
                await interaction.response.send_message(
                    embed=self.make_embed(
                        title="Wrong voice channel",
                        description="You must be in the same voice channel as the bot.",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
                return

        await interaction.response.defer()

        if interaction.guild.id not in guild_queues:
            guild_queues[interaction.guild.id] = OptimizedQueue()

        queue = guild_queues[interaction.guild.id]

        if not queue.queue:
            await self._ensure_playlist_cache()

            added = 0
            sample = random.sample(self._playlist_cache, min(10, len(self._playlist_cache)))
            async def _load_entry(entry):
                try:
                    info = await song_loader.extract_info_async(entry["url"])
                    song_data = info["entries"][0] if "entries" in info and info["entries"] else info
                    return await self.process_single_entry(song_data, requester=interaction.user)
                except Exception:
                    return None
            results = await asyncio.gather(*[_load_entry(e) for e in sample])
            for processed in results:
                if processed:
                    queue.add(processed)
                    added += 1

            if not added:
                await interaction.followup.send(
                    embed=self.make_embed(
                        title="Empty queue",
                        description="Could not fetch any songs.",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
        queue_list = list(queue.queue)
        random.shuffle(queue_list)
        queue.queue = deque(queue_list)

        embed = self.make_embed(
            title="Queue shuffled",
            description=f"{len(queue.queue)} songs reshuffled.",
            color=0x5865F2,
            author_name=interaction.user.display_name,
            author_icon=safe_avatar(interaction.user),
            footer="Enjoy!",
            footer_icon=safe_avatar(self.bot.user),
            thumbnail=safe_avatar(interaction.user)
        )

        wait_time = 0
        display_count = min(10, len(queue.queue))
        queue_as_list = list(queue.queue)
        for i, song_data in enumerate(queue_as_list[:display_count]):
            title = song_data['title']
            duration = song_data['duration']

            embed.add_field(
                name=f"{i + 1}. {title}",
                value=f"```\nDuration: {self.format_time(duration)} • Starts in: {self.format_time(wait_time)}\n```",
                inline=False
            )
            wait_time += duration

        total_duration = self.format_time(sum(song['duration'] for song in queue.queue))
        if len(queue.queue) > display_count:
            embed.add_field(
                name="More",
                value=f"```\n+{len(queue.queue) - display_count} more\nTotal duration: {total_duration}\n```",
                inline=False
            )
        else:
            embed.add_field(
                name="Summary",
                value=f"```\nTotal duration: {total_duration}\n```",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pause", description="Pauses or resumes the playback")
    async def pause(self, interaction: discord.Interaction):
        if await self.check_timeout_decorator(interaction):
            return
        voice_client = interaction.guild.voice_client

        if not voice_client or (not voice_client.is_playing() and not voice_client.is_paused()):
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Nothing playing",
                    description="Use /play to start music.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if not interaction.user.voice or interaction.user.voice.channel != voice_client.channel:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Wrong voice channel",
                    description="You must be in the same voice channel as the bot.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if voice_client.is_paused():
            voice_client.resume()
            if guild_state := self.currently_playing.get(interaction.guild.id):
                if guild_state.get('is_paused'):
                    guild_state['started_at'] = time.time() - guild_state.get('elapsed', 0)
                    guild_state['is_paused'] = False
            embed = self.make_embed(
                title="Resumed",
                description="Playback resumed.",
                color=0x2ecc71,
                author_name=interaction.user.display_name,
                author_icon=safe_avatar(interaction.user),
                footer="Use /pause to toggle",
                footer_icon=safe_avatar(self.bot.user)
            )
        else:
            voice_client.pause()
            if guild_state := self.currently_playing.get(interaction.guild.id):
                guild_state['elapsed'] = time.time() - guild_state.get('started_at', time.time())
                guild_state['is_paused'] = True
            embed = self.make_embed(
                title="Paused",
                description="Playback paused.",
                color=0xf39c12,
                author_name=interaction.user.display_name,
                author_icon=safe_avatar(interaction.user),
                footer="Use /pause to toggle",
                footer_icon=safe_avatar(self.bot.user)
            )

        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Handle bot being kicked/disconnected from voice channel
        if member.id == self.bot.user.id and before.channel is not None and after.channel is None:
            guild_id = before.channel.guild.id
            if guild_id in guild_queues:
                queue = guild_queues[guild_id]
                async with queue.lock:
                    queue.clear()
                    queue.playing = False
                    del guild_queues[guild_id]
            self.currently_playing.pop(guild_id, None)
            self._now_playing_messages.pop(guild_id, None)
            
            self.create_background_task(self._delayed_cleanup_task(guild_id))
            return

        # Handle users leaving voice channel (auto-disconnect when empty)
        if member.bot:
            return

        if before.channel and before.channel != after.channel:
            voice_client = before.channel.guild.voice_client
            if voice_client and voice_client.channel == before.channel:
                members_in_channel = [m for m in before.channel.members if not m.bot]
                if len(members_in_channel) == 0:
                    await asyncio.sleep(5)

                    if voice_client.is_connected():
                        current_members = [m for m in voice_client.channel.members if not m.bot]
                        if len(current_members) == 0:
                            guild_id = before.channel.guild.id
                            queue = guild_queues.get(guild_id)
                            if queue:
                                async with queue.lock:
                                    queue.clear()
                                    queue.playing = False
                                    del guild_queues[guild_id]
                            self.currently_playing.pop(guild_id, None)
                            self._now_playing_messages.pop(guild_id, None)
                            voice_channel = voice_client.channel
                            try:
                                await voice_channel.edit(status=None)
                            except Exception:
                                pass
                            await voice_client.disconnect(force=True)

    @app_commands.command(name="musicmute", description="Timeout a user from using music commands")
    @app_commands.describe(user="The user to timeout", duration="Duration in minutes")
    async def timeout_user_command(self, interaction: discord.Interaction, user: discord.Member, duration: int):
        await self.timeout_user(interaction, user, duration)

    async def timeout_user(self, interaction: discord.Interaction, user: discord.Member, duration: int):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="No permission",
                    description="You don't have permission to timeout users.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if duration <= 0 or duration > 10000:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Invalid duration",
                    description="Duration must be between 1 and 10000 minutes.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        end_time = datetime.now(timezone.utc) + timedelta(minutes=duration)

        self._timeout_cache[user.id] = {
            "user_id": user.id,
            "username": user.display_name,
            "timeout_by": interaction.user.id,
            "timeout_by_name": interaction.user.display_name,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": end_time.isoformat(),
            "duration_minutes": duration,
            "guild_id": interaction.guild.id
        }

        self._save_timeouts()

        embed = self.make_embed(
            title="User timed out",
            description=f"{user.display_name} has been muted from music commands.",
            color=0xe67e22,
            thumbnail=safe_avatar(user),
            fields=[
                ("Duration", f"```\n{duration} minutes\n```", True),
                ("Ends at", f"```\n{end_time.strftime('%H:%M:%S')}\n```", True),
                ("Moderator", f"```\n{interaction.user.display_name}\n```", True),
            ]
        )

        await interaction.response.send_message(embed=embed, ephemeral=True, delete_after=10, silent=True)

    def _load_timeouts(self):
        timeout_file = "timeouts.json"
        if not os.path.exists(timeout_file):
            self._timeout_cache = {}
            return
        try:
            with open(timeout_file, 'r') as f:
                self._timeout_cache = {int(k): v for k, v in json.load(f).items()}
        except (json.JSONDecodeError, FileNotFoundError):
            self._timeout_cache = {}

    def _save_timeouts(self):
        timeout_file = "timeouts.json"
        try:
            with open(timeout_file, 'w') as f:
                json.dump({str(k): v for k, v in self._timeout_cache.items()}, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving timeout file: {e}")

    def cleanup_expired_timeouts(self):
        current_time = datetime.now(timezone.utc)
        expired = [uid for uid, data in self._timeout_cache.items()
                   if datetime.fromisoformat(data["end_time"]) <= current_time]
        for uid in expired:
            del self._timeout_cache[uid]
        if expired:
            self._save_timeouts()

    def is_user_timed_out(self, user_id: int) -> Optional[dict]:
        self.cleanup_expired_timeouts()
        data = self._timeout_cache.get(user_id)
        if not data:
            return None
        end_time = datetime.fromisoformat(data["end_time"])
        if datetime.now(timezone.utc) > end_time:
            del self._timeout_cache[user_id]
            self._save_timeouts()
            return None
        return data

    async def check_timeout_decorator(self, interaction: discord.Interaction):
        timeout_data = self.is_user_timed_out(interaction.user.id)
        if timeout_data:
            end_time = datetime.fromisoformat(timeout_data["end_time"])
            remaining_minutes = max(0, int((end_time - datetime.now(timezone.utc)).total_seconds() / 60))
            embed = self.make_embed(
                title="Muted",
                description="You cannot use music commands right now.",
                color=0xe74c3c,
                fields=[
                    ("Time remaining", f"```\n{remaining_minutes} minutes\n```", True),
                    ("Ends at", f"```\n{end_time.strftime('%H:%M:%S')}\n```", True),
                    ("By", f"```\n{timeout_data['timeout_by_name']}\n```", True),
                ]
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return True
        return False

    @app_commands.command(name="unmusicmute", description="Remove timeout from a user")
    @app_commands.describe(user="The user to remove timeout from")
    async def untimeout_user(self, interaction: discord.Interaction, user: discord.Member):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="No permission",
                    description="You don't have permission to remove timeouts.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if user.id not in self._timeout_cache:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Not muted",
                    description=f"{user.display_name} is not currently muted.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        del self._timeout_cache[user.id]
        self._save_timeouts()

        embed = self.make_embed(
            title="Timeout removed",
            description=f"{user.display_name} can now use music commands again.",
            color=0x2ecc71,
            thumbnail=safe_avatar(user),
            fields=[
                ("Removed by", f"```\n{interaction.user.display_name}\n```", True)
            ]
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="musicmutelist", description="List all currently muted users")
    async def musicmute_list(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.kick_members:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="No permission",
                    description="You need `Kick Members` permission to view muted users.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        self.cleanup_expired_timeouts()

        if not self._timeout_cache:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="No mutes",
                    description="There are no currently muted users.",
                    color=0x5865F2
                ),
                ephemeral=True
            )
            return

        guild_mutes = [
            data for data in self._timeout_cache.values()
            if data.get("guild_id") == interaction.guild.id
        ]

        if not guild_mutes:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="No mutes",
                    description="There are no currently muted users in this server.",
                    color=0x5865F2
                ),
                ephemeral=True
            )
            return

        now = datetime.now()
        lines = []
        for data in guild_mutes:
            end = datetime.fromisoformat(data["end_time"])
            remaining = max(0, int((end - now).total_seconds() / 60))
            name = data.get("username", "Unknown")
            lines.append(
                f"**{name}** — {remaining}m remaining"
            )

        embed = self.make_embed(
            title=f"Muted users in {interaction.guild.name}",
            description="\n".join(lines),
            color=0xe74c3c,
            footer=f"Total: {len(guild_mutes)} muted"
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clearqueue", description="Vote to clear the entire queue")
    async def clear_queue(self, interaction: discord.Interaction):
        if await self.check_timeout_decorator(interaction):
            return

        voice_client = interaction.guild.voice_client

        if not interaction.user.voice:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Voice channel required",
                    description="Join a voice channel and try again.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if not voice_client or interaction.user.voice.channel != voice_client.channel:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Wrong voice channel",
                    description="You must be in the same voice channel as the bot to start a vote.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        queue = guild_queues.get(guild_id)

        if not queue or not queue.queue:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Queue is empty",
                    description="Nothing to clear.",
                    color=0x95a5a6
                ),
                ephemeral=True
            )
            return

        if interaction.user.guild_permissions.kick_members:
            cleared_count = len(queue.queue)
            total_duration = sum(song['duration'] for song in queue.queue)
            queue.clear()
            queue.playing = False
            try:
                voice_client.stop()
            except Exception:
                pass

            embed = self.make_embed(
                title="Queue cleared",
                description=f"Cleared by {interaction.user.display_name}.",
                color=0x2ecc71,
                thumbnail=safe_avatar(interaction.user),
                fields=[
                    ("Songs cleared", f"```\n{cleared_count}\n```", True),
                    ("Time removed", f"```\n{self.format_time(total_duration)}\n```", True)
                ]
            )
            await interaction.response.send_message(embed=embed)
            return

        voters = [m for m in voice_client.channel.members if not m.bot]
        total_voters = len(voters)
        if total_voters == 0:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="No voters",
                    description="No eligible voters in the voice channel.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if total_voters == 1:
            cleared_count = len(queue.queue)
            total_duration = sum(song['duration'] for song in queue.queue)
            queue.clear()
            queue.playing = False
            try:
                voice_client.stop()
            except Exception:
                pass
            embed = self.make_embed(
                title="Queue cleared",
                description=f"Cleared by {interaction.user.display_name}.",
                color=0x2ecc71,
                thumbnail=safe_avatar(interaction.user),
                fields=[
                    ("Songs cleared", f"```\n{cleared_count}\n```", True),
                    ("Time removed", f"```\n{self.format_time(total_duration)}\n```", True)
                ]
            )
            await interaction.response.send_message(embed=embed)
            return

        required = (total_voters // 2) + 1
        vote_duration = 20

        parent = self

        class VoteView(discord.ui.View):
            def __init__(self, voters_ids, required_count, timeout_seconds):
                super().__init__(timeout=timeout_seconds)
                self.voters = set(voters_ids)
                self.yes = set()
                self.no = set()
                self.required = required_count
                self.ended_early = False
                self.result_embed = None

            async def update_message_embed(self, message: discord.Message):
                try:
                    embed = message.embeds[0]
                    embed.description = (
                        f"{interaction.user.display_name} started a vote to clear the queue.\n\n"
                        f"Members in voice channel: {total_voters}\n"
                        f"Required votes to clear: {self.required}\n\n"
                        f"✅ Yes: {len(self.yes)} • ❌ No: {len(self.no)}\n\n"
                        f"Voting ends in {vote_duration} seconds."
                    )
                    await message.edit(embed=embed, view=self)
                except Exception:
                    pass

            @discord.ui.button(label="✅ Yes", style=discord.ButtonStyle.success)
            async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                uid = interaction.user.id
                if uid not in self.voters:
                    await interaction.response.send_message("You are not eligible to vote in this vote.", ephemeral=True)
                    return
                if uid in self.yes:
                    self.yes.remove(uid)
                    await interaction.response.send_message("Removed your ✅ vote.", ephemeral=True)
                else:
                    self.yes.add(uid)
                    self.no.discard(uid)
                    await interaction.response.send_message("Registered your ✅ vote.", ephemeral=True)
                await self.update_message_embed(interaction.message)

                if len(self.yes) >= self.required and not self.ended_early:
                    self.ended_early = True
                    cleared_count = len(queue.queue)
                    total_duration = sum(song['duration'] for song in queue.queue)
                    queue.clear()
                    queue.playing = False
                    try:
                        voice_client.stop()
                    except Exception:
                        pass

                    result_embed = parent.make_embed(
                        title="Vote passed",
                        description=f"Queue cleared ({len(self.yes)}/{total_voters} voted yes).",
                        color=0x2ecc71,
                        fields=[
                            ("Songs cleared", f"```\n{cleared_count}\n```", True),
                            ("Time removed", f"```\n{parent.format_time(total_duration)}\n```", True)
                        ]
                    )

                    self.result_embed = result_embed

                    for child in self.children:
                        child.disabled = True
                    try:
                        await interaction.message.edit(embed=result_embed, view=self)
                    except Exception:
                        pass

                    try:
                        await interaction.followup.send(embed=result_embed)
                    except Exception:
                        pass

                    self.stop()

            @discord.ui.button(label="❌ No", style=discord.ButtonStyle.danger)
            async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                uid = interaction.user.id
                if uid not in self.voters:
                    await interaction.response.send_message("You are not eligible to vote in this vote.", ephemeral=True)
                    return
                if uid in self.no:
                    self.no.remove(uid)
                    await interaction.response.send_message("Removed your ❌ vote.", ephemeral=True)
                else:
                    self.no.add(uid)
                    self.yes.discard(uid)
                    await interaction.response.send_message("Registered your ❌ vote.", ephemeral=True)
                await self.update_message_embed(interaction.message)

        vote_embed = self.make_embed(
            title="Vote to clear queue",
            description=(
                f"{interaction.user.display_name} started a vote to clear the queue.\n\n"
                f"Members in voice channel: {total_voters}\n"
                f"Required votes to clear: {required}\n\n"
                f"React by clicking a button. Voting ends in {vote_duration} seconds."
            ),
            color=0xf1c40f,
            thumbnail=safe_avatar(interaction.user)
        )

        view = VoteView([m.id for m in voters], required, vote_duration)

        await interaction.response.send_message(embed=vote_embed, view=view)
        vote_message = await interaction.original_response()

        await view.wait()

        if getattr(view, "ended_early", False):
            return

        for child in view.children:
            child.disabled = True
        try:
            await vote_message.edit(view=view)
        except Exception:
            pass

        yes_count = len(view.yes)
        no_count = len(view.no)

        yes_count = min(yes_count, total_voters)
        no_count = min(no_count, total_voters)

        if yes_count >= required:
            cleared_count = len(queue.queue)
            total_duration = sum(song['duration'] for song in queue.queue)
            queue.clear()
            queue.playing = False
            try:
                voice_client.stop()
            except Exception:
                pass

            result_embed = self.make_embed(
                title="Vote passed",
                description=f"Queue cleared ({yes_count}/{total_voters} voted yes).",
                color=0x2ecc71,
                fields=[
                    ("Songs cleared", f"```\n{cleared_count}\n```", True),
                    ("Time removed", f"```\n{self.format_time(total_duration)}\n```", True)
                ]
            )
        else:
            result_embed = self.make_embed(
                title="Vote failed",
                description=f"Not enough votes to clear the queue ({yes_count}/{total_voters} voted yes).",
                color=0x95a5a6,
                fields=[
                    ("Yes", f"```\n{yes_count}\n```", True),
                    ("No", f"```\n{no_count}\n```", True),
                    ("Required", f"```\n{required}\n```", True)
                ]
            )

        try:
            await vote_message.reply(embed=result_embed)
        except Exception:
            await interaction.followup.send(embed=result_embed)

    @app_commands.command(name="volume", description="Set playback volume (0-200%)")
    @app_commands.describe(level="Volume level (0-200, default 100)")
    async def volume(self, interaction: discord.Interaction, level: int):
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_playing():
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Nothing playing",
                    description="Play something first with /play.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if not interaction.user.voice or interaction.user.voice.channel != voice_client.channel:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Wrong voice channel",
                    description="You must be in the same voice channel as the bot.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        level = max(0, min(200, level))
        vol = level / 100.0
        guild_volumes[interaction.guild.id] = vol

        if voice_client.source and isinstance(voice_client.source, discord.PCMVolumeTransformer):
            voice_client.source.volume = vol

        embed = self.make_embed(
            title="Volume changed",
            description=f"Set volume to **{level}%**",
            color=0x2ecc71,
            author_name=interaction.user.display_name,
            author_icon=safe_avatar(interaction.user),
            fields=[
                ("Level", f"```\n{'█' * (level // 10)}{'░' * (20 - level // 10)}\n```" if level > 0 else "```\nMuted\n```", False),
            ]
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="move", description="Move a song to a different position in the queue")
    @app_commands.describe(from_position="Current position (1-based)", to_position="Target position (1-based)")
    async def move_song(self, interaction: discord.Interaction, from_position: int, to_position: int):
        queue = guild_queues.get(interaction.guild.id)
        if not queue or not queue.queue:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Queue is empty",
                    description="Nothing to move.",
                    color=0x95a5a6
                ),
                ephemeral=True
            )
            return

        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.channel:
            if not interaction.user.voice or interaction.user.voice.channel != voice_client.channel:
                await interaction.response.send_message(
                    embed=self.make_embed(
                        title="Wrong voice channel",
                        description="You must be in the same voice channel as the bot.",
                        color=0xe74c3c
                    ),
                    ephemeral=True
                )
                return

        n = len(queue.queue)
        if from_position < 1 or from_position > n:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Invalid position",
                    description=f"`from` must be between 1 and {n}.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        if to_position < 1 or to_position > n:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Invalid position",
                    description=f"`to` must be between 1 and {n}.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        from_idx = from_position - 1
        to_idx = to_position - 1

        if from_idx == to_idx:
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Same position",
                    description="The song is already at that position.",
                    color=0xf39c12
                ),
                ephemeral=True
            )
            return

        song = queue.queue[from_idx]
        del queue.queue[from_idx]
        queue.queue.insert(to_idx, song)

        embed = self.make_embed(
            title="Song moved",
            description=f"**{song['title']}** moved from #{from_position} to #{to_position}.",
            color=0x2ecc71,
            thumbnail=song.get('thumbnail'),
            author_name=interaction.user.display_name,
            author_icon=safe_avatar(interaction.user),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="playing", description="Shows current song progress, info and lyrics")
    async def playing(self, interaction: discord.Interaction):
        if await self.check_timeout_decorator(interaction):
            return

        guild_state = self.currently_playing.get(interaction.guild.id)
        voice_client = interaction.guild.voice_client

        if not guild_state or not voice_client or not voice_client.is_connected() or (not voice_client.is_playing() and not voice_client.is_paused()):
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Nothing playing",
                    description="Use /play to start music.",
                    color=0x95a5a6
                ),
                ephemeral=True
            )
            return

        song_data = guild_state['song_data']
        duration = song_data.get('duration', 0)

        # Calculate current position
        if guild_state.get('is_paused'):
            elapsed = guild_state.get('elapsed', 0)
        else:
            elapsed = time.time() - guild_state.get('started_at', time.time())
        elapsed = max(0, min(elapsed, duration)) if duration > 0 else elapsed

        # Build progress bar
        bar_length = 20
        if duration > 0:
            filled = int((elapsed / duration) * bar_length)
            filled = max(0, min(bar_length, filled))
        else:
            filled = 0
        bar = '▬' * filled + '🔘' + '▬' * (bar_length - filled - 1)

        # Status indicator
        if voice_client.is_paused():
            status = "⏸️ Paused"
        else:
            status = f"{DANCE_EMOJI} Playing"

        source = song_data.get('source', {'label': 'Unknown', 'icon': '🔗'})
        source_icon = source.get('icon', '🔗')
        source_label = source.get('label', 'Unknown')

        fields = [
            ("Progress", f"{bar}\n`{self.format_time(elapsed)}` / `{self.format_time(duration)}`", False),
            ("Artist", f"{song_data.get('author', 'Unknown')}", True),
            ("Platform", f"{source_icon} {source_label}", True),
            ("Status", status, True),
        ]

        if song_data.get('views'):
            fields.append(("Views", f"👁️ {song_data['views']:,}", True))
        if song_data.get('likes'):
            fields.append(("Likes", f"👍 {song_data['likes']:,}", True))

        # Queue info
        queue = guild_queues.get(interaction.guild.id)
        if queue and queue.queue:
            fields.append(("Up next", f"🎵 {queue.queue[0]['title'][:40]}", True))
            fields.append(("In queue", f"{len(queue.queue)} song{'s' if len(queue.queue) != 1 else ''}", True))

        embed = self.make_embed(
            title="Now Playing",
            description=f"{source_icon} **{song_data.get('title', 'Unknown')}**\n[Open]({song_data.get('song_url', '#')})",
            color=0x5865F2,
            thumbnail=song_data.get('thumbnail'),
            author_name=f"Requested by {song_data.get('requested_by_name', 'Unknown')}",
            author_icon=None,
            footer="Use /skip to skip • /pause to pause",
            footer_icon=safe_avatar(self.bot.user),
            fields=fields
        )

        lyrics_view = LyricsButtonView(self, song_data.get('title', ''), song_data.get('author', ''))
        await interaction.response.send_message(embed=embed, view=lyrics_view)

    @app_commands.command(name="seek", description="Jump to a specific position in the current song")
    @app_commands.describe(seconds="Position in seconds to jump to")
    async def seek(self, interaction: discord.Interaction, seconds: int):
        if await self.check_timeout_decorator(interaction):
            return

        voice_client = interaction.guild.voice_client
        if not voice_client or (not voice_client.is_playing() and not voice_client.is_paused()):
            await interaction.response.send_message(
                embed=self.make_embed(title="Nothing playing", description="Use /play to start music.", color=0xe74c3c),
                ephemeral=True
            )
            return

        if not interaction.user.voice or interaction.user.voice.channel != voice_client.channel:
            await interaction.response.send_message(
                embed=self.make_embed(title="Wrong voice channel", description="You must be in the same voice channel as the bot.", color=0xe74c3c),
                ephemeral=True
            )
            return

        guild_state = self.currently_playing.get(interaction.guild.id)
        if not guild_state:
            await interaction.response.send_message(
                embed=self.make_embed(title="Nothing playing", description="No active song to seek.", color=0xe74c3c),
                ephemeral=True
            )
            return

        song_data = guild_state['song_data']
        duration = song_data.get('duration', 0)
        if seconds < 0 or (duration > 0 and seconds >= duration):
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Invalid position",
                    description=f"Please enter a value between 0 and {int(duration)} seconds.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        webpage_url = song_data.get('song_url')
        if not webpage_url:
            await interaction.response.send_message(
                embed=self.make_embed(title="Error", description="No stream URL available.", color=0xe74c3c),
                ephemeral=True
            )
            return

        await interaction.response.defer()

        was_paused = voice_client.is_paused()

        try:
            voice_client.stop()

            fresh_info = await song_loader.extract_info_async(webpage_url)
            stream_url = fresh_info.get('url')
            if not stream_url:
                await interaction.followup.send(
                    embed=self.make_embed(title="Error", description="Could not get stream URL.", color=0xe74c3c),
                    ephemeral=True
                )
                return

            def create_seek_source():
                ffmpeg_args = {
                    'before_options': f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss {seconds}',
                    'options': '-vn -bufsize 512k'
                }
                return discord.FFmpegPCMAudio(stream_url, **ffmpeg_args)

            loop = asyncio.get_running_loop()
            audio_source = await loop.run_in_executor(song_loader.executor, create_seek_source)

            volume = guild_volumes.get(interaction.guild.id, 1.0)
            audio_source = discord.PCMVolumeTransformer(audio_source, volume=volume)

            queue = guild_queues.get(interaction.guild.id)

            def after_song(e):
                if e:
                    logger.error(f"Playback error: {e}")
                if queue:
                    loop_mode = self.guild_loops.get(interaction.guild.id, 'off')
                    if loop_mode == 'song' and song_data:
                        queue.queue.appendleft(song_data)
                    elif loop_mode == 'queue' and song_data:
                        queue._loop_played.append(song_data)
                        if queue.is_empty() and queue._loop_played:
                            for s in queue._loop_played:
                                queue.add(s)
                            queue._loop_played.clear()
                    queue.playing = False
                pn = self.play_next(interaction.guild, voice_client, interaction)
                asyncio.run_coroutine_threadsafe(pn, self.bot.loop)

            voice_client.play(audio_source, after=after_song)

            guild_state['started_at'] = time.time() - seconds
            guild_state['elapsed'] = seconds
            guild_state['is_paused'] = False
            if queue:
                queue.playing = True

            if was_paused:
                voice_client.pause()
                guild_state['is_paused'] = True
                guild_state['elapsed'] = seconds

            seek_pos = self.format_time(seconds)
            seek_total = self.format_time(duration)
            await interaction.followup.send(
                embed=self.make_embed(
                    title="Seeked",
                    description=f"Jumped to `{seek_pos}` / `{seek_total}`",
                    color=0x3498db,
                    author_name=interaction.user.display_name,
                    author_icon=safe_avatar(interaction.user)
                )
            )

        except Exception as e:
            logger.error(f"Seek error: {e}")
            await interaction.followup.send(
                embed=self.make_embed(title="Error", description=f"Could not seek.\n\n{e}", color=0xe74c3c),
                ephemeral=True
            )

    @app_commands.command(name="loop", description="Toggle loop mode (off, current song, or entire queue)")
    @app_commands.describe(mode="Loop mode")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Song", value="song"),
        app_commands.Choice(name="Queue", value="queue"),
    ])
    async def loop(self, interaction: discord.Interaction, mode: str = None):
        if await self.check_timeout_decorator(interaction):
            return

        guild_id = interaction.guild.id
        current = self.guild_loops.get(guild_id, 'off')

        if mode is None:
            # Cycle: off -> song -> queue -> off
            cycle = {'off': 'song', 'song': 'queue', 'queue': 'off'}
            mode = cycle[current]

        self.guild_loops[guild_id] = mode

        mode_labels = {
            'off': ('⭕ Loop disabled', 'Songs will play normally.', 0x95a5a6),
            'song': ('🔂 Song loop enabled', 'The current song will repeat.', 0xf39c12),
            'queue': ('🔁 Queue loop enabled', 'The entire queue will repeat.', 0x3498db),
        }
        title, desc, color = mode_labels[mode]

        await interaction.response.send_message(
            embed=self.make_embed(
                title=title,
                description=desc,
                color=color,
                author_name=interaction.user.display_name,
                author_icon=safe_avatar(interaction.user)
            )
        )

    @app_commands.command(name="remove", description="Remove a song from the queue by position")
    @app_commands.describe(position="Position number in the queue (starting from 1)")
    async def remove(self, interaction: discord.Interaction, position: int):
        if await self.check_timeout_decorator(interaction):
            return

        queue = guild_queues.get(interaction.guild.id)
        if not queue or not queue.queue:
            await interaction.response.send_message(
                embed=self.make_embed(title="Queue empty", description="There are no songs in the queue.", color=0xe74c3c),
                ephemeral=True
            )
            return

        if position < 1 or position > len(queue.queue):
            await interaction.response.send_message(
                embed=self.make_embed(
                    title="Invalid position",
                    description=f"Please enter a number between 1 and {len(queue.queue)}.",
                    color=0xe74c3c
                ),
                ephemeral=True
            )
            return

        removed_song = queue.queue[position - 1]
        del queue.queue[position - 1]

        await interaction.response.send_message(
            embed=self.make_embed(
                title="Removed",
                description=f"Removed **{removed_song.get('title', 'Unknown')}** from position {position}.",
                color=0xe74c3c,
                thumbnail=removed_song.get('thumbnail'),
                author_name=interaction.user.display_name,
                author_icon=safe_avatar(interaction.user)
            )
        )


    @playlist_group.command(name="create", description="Create a new playlist")
    async def playlist_create(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        success = await create_playlist(interaction.guild.id, name)
        if success:
            await interaction.followup.send(f"Playlist **{name}** created for this server.")
        else:
            await interaction.followup.send(f"Playlist **{name}** already exists.")

    @playlist_group.command(name="delete", description="Delete a playlist")
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        success = await delete_playlist(interaction.guild.id, name)
        if success:
            await interaction.followup.send(f"Playlist **{name}** deleted.")
        else:
            await interaction.followup.send(f"Playlist **{name}** not found.")

    @playlist_group.command(name="add", description="Add a song to the server playlist")
    async def playlist_add(self, interaction: discord.Interaction, name: str, url: str):
        await interaction.response.defer(ephemeral=True)
        try:
            if needs_resolution(url):
                direct = await resolve_to_direct_url(url)
                if direct:
                    url = direct
            info = await song_loader.extract_info_async(url)
            if not info:
                await interaction.followup.send(f"Could not extract info from URL: **{url}**")
                return
            song_data = info["entries"][0] if "entries" in info and info["entries"] else info
            if not song_data:
                await interaction.followup.send(f"Could not extract song data from URL: **{url}**")
                return
            title = song_data.get("title", "Unknown")
            song_url = song_data.get("webpage_url") or song_data.get("original_url") or url
            success = await add_to_playlist(interaction.guild.id, name, song_url, title)
            if success:
                await interaction.followup.send(f"Added **{title}** to playlist **{name}**.")
            else:
                await interaction.followup.send(f"Playlist **{name}** not found. Use `/playlist create` first.")
        except Exception as e:
            await interaction.followup.send(f"Failed to extract info from URL: {e}")

    @playlist_group.command(name="import", description="Import a YouTube/Spotify playlist")
    async def playlist_import(self, interaction: discord.Interaction, name: str, url: str):
        await interaction.response.defer(ephemeral=True)
        if await get_playlist(interaction.guild.id, name) is None:
            await interaction.followup.send(f"Playlist **{name}** not found. Create it first.")
            return
            
        try:
            tracks = []
            
            # Step 1: Resolving tracks
            if is_playlist(url):
                resolved = await resolve_playlist(url)
                if not resolved:
                    await interaction.followup.send("Could not resolve playlist. Make sure the URL is valid.")
                    return
                    
                loading_msg = await interaction.followup.send(
                    embed=self.make_embed(
                        title="🔍 Resolving Tracks",
                        description=f"Found **{len(resolved)}** tracks\nConverting to playable URLs...",
                        color=0x3498db
                    )
                )
                
                failed_count = 0
                total = len(resolved)
                BATCH_SIZE = 5

                for batch_start in range(0, total, BATCH_SIZE):
                    batch = resolved[batch_start:batch_start + BATCH_SIZE]

                    async def _resolve_one(t):
                        query = t.get("query", "")
                        video_url = t.get("url", "")
                        video_id = t.get("id", "")
                        video_title = t.get("title", "Unknown")
                        final_url = ""

                        if query and query.startswith("ytsearch:"):
                            try:
                                search_info = await song_loader.extract_info_async(query)
                                if search_info and "entries" in search_info and search_info["entries"]:
                                    first_result = search_info["entries"][0]
                                    final_url = first_result.get("webpage_url") or first_result.get("original_url") or first_result.get("url", "")
                            except Exception:
                                return None
                        elif video_id:
                            final_url = f"https://www.youtube.com/watch?v={video_id}"
                        elif video_url and ("youtube.com/watch" in video_url or "youtu.be" in video_url):
                            final_url = video_url
                        elif video_url and "googlevideo.com" in video_url:
                            id_match = re.search(r'[?&]id=([\w-]{11})', video_url)
                            if id_match:
                                final_url = f"https://www.youtube.com/watch?v={id_match.group(1)}"

                        if final_url:
                            return {"url": final_url, "title": video_title}
                        return None

                    results = await asyncio.gather(*[_resolve_one(t) for t in batch], return_exceptions=True)

                    for r in results:
                        if isinstance(r, dict):
                            tracks.append(r)
                        else:
                            failed_count += 1

                    processed = min(batch_start + BATCH_SIZE, total)
                    try:
                        await loading_msg.edit(
                            embed=self.make_embed(
                                title="🔍 Resolving Tracks",
                                description=f"Resolving **{name}**...\nProcessed {processed}/{total} tracks",
                                color=0x3498db,
                                fields=[
                                    ("Resolved", f"```\n{len(tracks)}\n```", True),
                                    ("Failed", f"```\n{failed_count}\n```", True),
                                ]
                            )
                        )
                    except Exception:
                        pass
            else:
                # Single URL or non-playlist
                loading_msg = await interaction.followup.send(
                    embed=self.make_embed(
                        title="📥 Importing",
                        description="Fetching tracks...",
                        color=0x3498db
                    )
                )
                
                info = await song_loader.extract_info_async(url)
                if info and "entries" in info:
                    for t in info["entries"]:
                        if t:
                            track_url = t.get("webpage_url") or t.get("original_url") or t.get("url", "")
                            if track_url and "googlevideo.com" not in track_url:
                                tracks.append({"url": track_url, "title": t.get("title", "Unknown")})
            
            # Step 2: Save to database
            if not tracks:
                await loading_msg.edit(
                    embed=self.make_embed(
                        title="❌ Import Failed",
                        description="No tracks found to import.",
                        color=0xe74c3c
                    )
                )
                return
            
            await import_playlist_tracks(interaction.guild.id, name, tracks)
            
            # Step 3: Final summary
            success_count = len(tracks)
            await loading_msg.edit(
                embed=self.make_embed(
                    title="✅ Import Complete",
                    description=f"Successfully imported **{success_count}** track{'s' if success_count != 1 else ''} to **{name}**.",
                    color=0x2ecc71,
                    fields=[
                        ("Playlist", f"```\n{name}\n```", True),
                        ("Tracks", f"```\n{success_count}\n```", True),
                    ]
                )
            )
            
        except Exception as e:
            logger.error(f"Playlist import failed: {e}")
            await interaction.followup.send(f"Failed to import playlist: {e}")

    @playlist_group.command(name="list", description="List server playlists")
    async def playlist_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        playlists = await get_guild_playlists(interaction.guild.id)
        if not playlists:
            await interaction.followup.send("This server doesn't have any playlists.")
        else:
            msg = "**Server Playlists:**\n" + "\n".join(f"- {p}" for p in playlists)
            await interaction.followup.send(msg)

    @playlist_group.command(name="play", description="Play a saved playlist")
    async def playlist_play(self, interaction: discord.Interaction, name: str):
        if await self.check_timeout_decorator(interaction):
            return
        await interaction.response.defer()

        if interaction.guild.id not in guild_queues:
            guild_queues[interaction.guild.id] = OptimizedQueue()
        queue = guild_queues[interaction.guild.id]

        voice_client = await self._ensure_voice(interaction, queue)
        if not voice_client:
            await interaction.followup.send("You are not in a voice channel.", ephemeral=True)
            return

        tracks = await get_playlist(interaction.guild.id, name)
        if tracks is None:
            await interaction.followup.send(f"Playlist **{name}** not found.", ephemeral=True)
            return
        if not tracks:
            await interaction.followup.send(f"Playlist **{name}** is empty.", ephemeral=True)
            return

        loading_msg = await interaction.followup.send(
            embed=self.make_embed(
                title="📂 Loading Playlist",
                description=f"Loading **{name}** ({len(tracks)} tracks)...",
                color=0x3498db
            )
        )

        added = 0
        failed = 0
        playback_started = False

        BATCH_SIZE = 5
        for batch_start in range(0, len(tracks), BATCH_SIZE):
            batch = tracks[batch_start:batch_start + BATCH_SIZE]
            urls = [t.get("url", "") for t in batch if t.get("url")]

            results = await asyncio.gather(
                *[self._quick_add_song(u, requester=interaction.user) for u in urls],
                return_exceptions=True
            )

            for result in results:
                if isinstance(result, Exception) or not result:
                    failed += 1
                    continue
                queue.add(result)
                added += 1

                if not playback_started and not voice_client.is_playing() and not voice_client.is_paused():
                    playback_started = True
                    asyncio.create_task(self.play_next(interaction.guild, voice_client, interaction))

            try:
                await loading_msg.edit(embed=self.make_embed(
                    title="📂 Loading Playlist",
                    description=f"Loading **{name}**... {added + failed}/{len(tracks)}",
                    color=0x3498db,
                    fields=[("Added", f"```\n{added}\n```", True), ("Failed", f"```\n{failed}\n```", True)]
                ))
            except Exception:
                pass

        await loading_msg.edit(embed=self.make_embed(
            title="✅ Playlist Loaded",
            description=f"Added **{added}** songs from **{name}** to the queue.",
            color=0x2ecc71,
            fields=[("Added", f"```\n{added}\n```", True), ("Failed", f"```\n{failed}\n```", True)]
        ))

    @dynamic_playlist_group.command(name="create", description="Create a dynamic playlist from a source URL")
    async def dynamic_playlist_create(self, interaction: discord.Interaction, name: str, url: str):
        await interaction.response.defer(ephemeral=True)
        success, msg = await create_dynamic_playlist(interaction.user.id, name, url)
        await interaction.followup.send(msg)

    @dynamic_playlist_group.command(name="delete", description="Delete a dynamic playlist")
    async def dynamic_playlist_delete(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        success = await delete_dynamic_playlist(interaction.user.id, name)
        if success:
            await interaction.followup.send(f"Dynamic playlist **{name}** deleted.")
        else:
            await interaction.followup.send(f"Dynamic playlist **{name}** not found.")

    @dynamic_playlist_group.command(name="list", description="List your dynamic playlists")
    async def dynamic_playlist_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        playlists = await get_user_dynamic_playlists(interaction.user.id)
        if not playlists:
            await interaction.followup.send("You don't have any dynamic playlists.")
        else:
            lines = []
            for p in playlists:
                lines.append(f"**{p['name']}** — {p['source_type']}")
            await interaction.followup.send("**Your Dynamic Playlists:**\n" + "\n".join(lines))

    @dynamic_playlist_group.command(name="play", description="Play a dynamic playlist (fetches tracks live)")
    async def dynamic_playlist_play(self, interaction: discord.Interaction, name: str):
        if await self.check_timeout_decorator(interaction):
            return
        await interaction.response.defer()

        if interaction.guild.id not in guild_queues:
            guild_queues[interaction.guild.id] = OptimizedQueue()
        queue = guild_queues[interaction.guild.id]

        voice_client = await self._ensure_voice(interaction, queue)
        if not voice_client:
            await interaction.followup.send("You are not in a voice channel.", ephemeral=True)
            return

        info = await get_dynamic_playlist(interaction.user.id, name)
        if info is None:
            await interaction.followup.send(f"Dynamic playlist **{name}** not found.", ephemeral=True)
            return

        loading_msg = await interaction.followup.send(
            embed=self.make_embed(
                title="📂 Loading Dynamic Playlist",
                description=f"Fetching tracks from **{info['source_type']}**...",
                color=0x3498db
            )
        )
        tracks = await fetch_dynamic_tracks(info["source_url"], info["source_type"])

        if not tracks:
            await loading_msg.edit(embed=self.make_embed(
                title="❌ Empty Playlist",
                description="No tracks found in the source playlist.",
                color=0xe74c3c
            ))
            return

        added = 0
        failed = 0
        playback_started = False

        BATCH_SIZE = 5
        for batch_start in range(0, len(tracks), BATCH_SIZE):
            batch = tracks[batch_start:batch_start + BATCH_SIZE]
            urls = [t.get("url", "") for t in batch if t.get("url")]

            results = await asyncio.gather(
                *[self._quick_add_song(u, requester=interaction.user) for u in urls],
                return_exceptions=True
            )

            for result in results:
                if isinstance(result, Exception) or not result:
                    failed += 1
                    continue
                queue.add(result)
                added += 1

                if not playback_started and not voice_client.is_playing() and not voice_client.is_paused():
                    playback_started = True
                    asyncio.create_task(self.play_next(interaction.guild, voice_client, interaction))

            try:
                await loading_msg.edit(embed=self.make_embed(
                    title="📂 Loading Dynamic Playlist",
                    description=f"Loading **{name}**... {added + failed}/{len(tracks)}",
                    color=0x3498db,
                    fields=[("Added", f"```\n{added}\n```", True), ("Failed", f"```\n{failed}\n```", True)]
                ))
            except Exception:
                pass

        await loading_msg.edit(embed=self.make_embed(
            title="✅ Dynamic Playlist Loaded",
            description=f"Added **{added}** songs from **{name}** to the queue.",
            color=0x2ecc71,
            fields=[("Added", f"```\n{added}\n```", True), ("Failed", f"```\n{failed}\n```", True)]
        ))

    @app_commands.command(name="purge", description="Purge bot messages from a channel")
    @app_commands.describe(channel="purge messages from a Channel (default: current channel)")
    async def purge_test(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        await interaction.response.defer(ephemeral=True)
        
        target_channel = channel or interaction.channel
        
        try:
            deleted_count = await purge_channel(target_channel)
            await interaction.followup.send(f"✅ Successfully purged **{deleted_count}** bot messages from {target_channel.mention}.")
            
            # Resend static music embed if purged channel is the music channel
            if str(target_channel.id) == str(I_CHANNEL):
                await self.send_static_message()
                      
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to purge messages: {e}")

    @app_commands.command(name="stats", description="Show bot statistics")
    async def botstats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        uptime = discord.utils.utcnow() - self.bot_start_time
        days, remainder = divmod(int(uptime.total_seconds()), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        voice_connections = len(self.bot.voice_clients)
        total_queued = sum(len(q.queue) for q in guild_queues.values() if hasattr(q, 'queue'))
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count or 0 for g in self.bot.guilds)

        embed = discord.Embed(
            title="📊 Bot Statistics",
            color=0x5865F2
        )
        embed.add_field(name="⏱ Uptime", value=f"{days}d {hours}h {minutes}m {seconds}s", inline=True)
        embed.add_field(name="🎵 Songs Played", value=str(self.songs_played), inline=True)
        embed.add_field(name="🌐 Servers", value=str(guild_count), inline=True)
        embed.add_field(name="👥 Users", value=str(user_count), inline=True)
        embed.add_field(name="🔊 Voice Connections", value=str(voice_connections), inline=True)
        embed.add_field(name="📋 Queued Songs", value=str(total_queued), inline=True)
        embed.add_field(name="📡 Latency", value=f"{self.bot.latency*1000:.0f}ms", inline=True)

        await interaction.followup.send(embed=embed)

    async def cog_load(self):
        self.bot.tree.add_command(self.playlist_group, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.dynamic_playlist_group, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.play, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.skip, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.list, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.leave, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.shuffle, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.play_chart, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.pause, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.timeout_user_command, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.untimeout_user, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.clear_queue, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.volume, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.move_song, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.playing, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.seek, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.loop, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.remove, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.botstats, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.musicmute_list, guild=discord.Object(id=SYNC_SERVER))
        self.bot.tree.add_command(self.purge_test, guild=discord.Object(id=SYNC_SERVER))

    async def cog_unload(self):
        for task in self.background_tasks:
            if not task.done():
                task.cancel()
        song_loader.executor.shutdown(wait=False)
