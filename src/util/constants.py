import discord
from dotenv import dotenv_values
import os

SET_VC_STATUS_TO_MUSIC_PLAYING = False # Set to True, if the bot should change the VC status

AUTO_PLAY_ENABLED = True  # Set to True to enable autoplay feature in MusicCog (BETA)

#---------------------------------------------------------------------------------------------#
#---------------------------------------------------------------------------------------------#

# Load config stuff
_config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
print("Looking for .env at:", _config_path)
print("Exists:", os.path.exists(_config_path))

_config = dotenv_values(_config_path)
TOKEN = _config.get('DISCORD_TOKEN')
SYNC_SERVER = _config.get('SERVER')
I_CHANNEL = _config.get('I_CHANNEL')

# Emojis for the bot
CHECK = "<:check:1368203772123283506>"
UNCHECK = "<:X_:1373405777297014944>"
LOADING_EMOJI = "<a:2923printsdark:1367119727763259533>"
DANCE_EMOJI = "<a:dance:1369716119073587290>"

# Button Styles
DANGER = discord.ButtonStyle.danger
SECONDARY = discord.ButtonStyle.secondary
GREEN = discord.ButtonStyle.green
PURPLE = discord.ButtonStyle.blurple

# YT_OPTS
YT_OPTS = {
    'format': 'bestaudio*',
    'default_search': 'auto',
    'noplaylist': False,
    'quiet': True,
    'no_warnings': True,
    'allow_unplayable_formats': True,
    'ignoreerrors': True,
    'cachedir': False,
    'restrictfilenames': True,
    'socket_timeout': 15,
    'retries': 10,
    'fragment_retries': 10,
    'extractor_retries': 5,
    'skip_unavailable_fragments': True,
    'geo_bypass': True,
    'include_thumbnail': True,
    'outtmpl': '-',
    'prefer_ffmpeg': True,
    'extractor_args': {
        'youtube': {
            'js_runtime': ['node', 'deno'],
            'skip': ['dash', 'hls'],
        },
        'spotify': {'mode': ['search']},
        'soundcloud': {},
        'deezer': {},
        'tidal': {},
    },
    'postprocessors': [
        {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'opus',
            'preferredquality': '128',
        },
        {'key': 'FFmpegMetadata'},
    ],
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
}

# Embed
EMBED_FOOTER = "❤️ Music | by nino161er"