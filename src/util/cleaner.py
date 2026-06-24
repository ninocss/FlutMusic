import asyncio
from datetime import datetime, timedelta, timezone
import discord

async def purge_channel(channel):
    vor_24_stunden = datetime.now(timezone.utc) - timedelta(days=1)
    
    def check(message):
        return message.created_at > vor_24_stunden

    deleted_messages = await channel.purge(limit=100, check=check, bulk=True)
    return len(deleted_messages)
