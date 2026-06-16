import discord

from typing import Optional

def simple_embed(text: str, thumbnail: Optional[str] = None, color: int = 0x00ff00):
    embed = discord.Embed(description=text, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed