# ruff: noqa: F403 F405
import discord
from discord.ui import View, Button
from util.constants import *
from util.lyrics import fetch_lyrics
from typing import TYPE_CHECKING
import re
import logging
import colorlog

if TYPE_CHECKING:
    from cogs.music import MusicCog

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    '%(name_log_color)s%(name)s%(reset)s: [%(levelname)s] %(message_log_color)s%(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'cyan',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    },
    secondary_log_colors={
        'message': {
            'DEBUG': 'white',
            'INFO': 'white',
            'WARNING': 'white',
            'ERROR': 'white',
            'CRITICAL': 'white',
        },
        'name': {
            'DEBUG': 'light_black',
            'INFO': 'light_black',
            'WARNING': 'light_black',
            'ERROR': 'light_black',
            'CRITICAL': 'light_black',
        }
    }
))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

class ActionsView(View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.song_history = []

        ins_song_btn = Button(label="Inspire Me", emoji="✨", style=GREEN, custom_id="ran_song_btn", row=0)
        ins_song_btn.callback = self.ran_song

        mostplayed_btn = Button(label="Most Played", emoji="🏆", style=PURPLE, custom_id="mostplayed_btn", row=0)
        mostplayed_btn.callback = self.mostplayed

        charts_btn = Button(label="Charts", emoji="🎶", style=SECONDARY, custom_id="charts_btn", row=1)
        charts_btn.callback = self.charts_song

        history_btn = Button(label="History", emoji="📖", style=SECONDARY, custom_id="history_btn", row=1)
        history_btn.callback = self.history_call

        self.add_item(ins_song_btn)
        self.add_item(mostplayed_btn)
        self.add_item(charts_btn)
        self.add_item(history_btn)

    async def mostplayed(self, interaction: discord.Interaction):
        history = await self.get_history(interaction)

        if not history:
            embed = discord.Embed(
                title="❌ No History Found",
                description="I couldn't find any songs in the recent history.",
                color=0xff0000
            )
            embed.set_footer(text="Try playing some music first!")
            embed.timestamp = discord.utils.utcnow()
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        song_counts = {}
        for song in history:
            song_counts[song] = song_counts.get(song, 0) + 1

        sorted_songs = sorted(song_counts.items(), key=lambda x: x[1], reverse=True)

        lines = []
        for i, (song, count) in enumerate(sorted_songs[:10], 1):
            rank_emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🎵"
            lines.append(f"{rank_emoji} **{i}.** {song} • `{count}×`")

        embed = discord.Embed(
            title="🏆 Most Played Songs",
            description="\n".join(lines),
            color=0xff6b6b
        )
        embed.add_field(
            name="📊 Statistics",
            value=f"• Unique songs: **{len(song_counts)}**\n• Total plays: **{sum(song_counts.values())}**",
            inline=True
        )
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_footer(text="Tap a button below to play a top track.")
        embed.timestamp = discord.utils.utcnow()

        view = self.MostPlayedView(self.bot, sorted_songs[:3])
        await interaction.followup.send(embed=embed, view=view)

    class MostPlayedView(View):
        def __init__(self, bot, top_songs):
            super().__init__(timeout=300)
            self.bot = bot

            for i, (song, _) in enumerate(top_songs):
                display_name = song[:40] + "…" if len(song) > 40 else song
                rank_emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
                button = Button(
                    label=f"{display_name}",
                    style=GREEN,
                    emoji=rank_emoji,
                    row=0
                )
                button.callback = self.create_play_callback(song)
                self.add_item(button)

            refresh_btn = Button(label="Refresh", emoji="🔄", style=SECONDARY, row=1)
            refresh_btn.callback = self.refresh_callback
            self.add_item(refresh_btn)

        def create_play_callback(self, song: str):
            async def play_callback(interaction: discord.Interaction):
                music_cog = self.bot.get_cog("MusicCog")
                if music_cog:
                    await music_cog.mostplayed_callback(interaction, song)
                else:
                    embed = discord.Embed(
                        title="Error",
                        description="Music system is currently unavailable.",
                        color=0xff0000
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
            return play_callback

        async def refresh_callback(self, interaction: discord.Interaction):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Refreshed",
                    description="Please re-open Most Played to fetch latest stats.",
                    color=0x4ecdc4
                ),
                ephemeral=True,
                delete_after=6
            )

    async def get_history(self, interaction: discord.Interaction) -> list:
        if not interaction.response.is_done():
            await interaction.response.defer()
        history_list = []

        channel = None
        try:
            channel = await self.bot.fetch_channel(I_CHANNEL)
        except Exception as e:
            logger.error(f"get_history fetch_channel error: {e}")

        if channel:
            try:
                async for message in channel.history(limit=300):
                    if (
                    message.author == self.bot.user and
                    message.embeds
                    ):
                        embed = message.embeds[0]
                        if embed.title and embed.title.lower().strip() == "now playing":
                            desc = embed.description or ""
                            song_name = None

                            m = re.search(r"\*\*(.*?)\*\*", desc)
                            if m:
                                song_name = m.group(1).strip()
                            else:
                                idx = desc.lower().find("now playing:")
                                if idx != -1:
                                    after = desc[idx + len("now playing:"):].strip()
                                    song_name = after.splitlines()[0].strip().strip("* ").strip()
                                else:
                                    song_name = desc.splitlines()[0].strip()

                            if song_name:
                                history_list.append(song_name)
            except Exception as e:
                logger.error(f"get_history history parse error: {e}")

        self.song_history = history_list[::-1]
        return self.song_history

    async def ran_song(self, interaction: discord.Interaction):
        music_cog: "MusicCog" = self.bot.get_cog("MusicCog")
        if music_cog:
            await music_cog.inspire_me(interaction)
        else:
            embed = discord.Embed(
                title="Error",
                description="Music system is currently unavailable.",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def charts_song(self, interaction: discord.Interaction):
        music_cog: "MusicCog" = self.bot.get_cog("MusicCog")
        if music_cog:
            await music_cog.play_chart.callback(music_cog, interaction)
        else:
            embed = discord.Embed(
                title="Error",
                description="Music system is currently unavailable.",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def history_call(self, interaction: discord.Interaction):
        current_history = await self.get_history(interaction=interaction)

        if not current_history:
            embed = discord.Embed(
                title="No History Found",
                description="I couldn't find any songs in the recent history.",
                color=0xff0000
            )
            embed.set_footer(text="Try playing some music first!")
            embed.timestamp = discord.utils.utcnow()
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        page_size = 10
        total_pages = (len(current_history) + page_size - 1) // page_size

        def create_history_embed(page: int = 0):
            start_idx = page * page_size
            end_idx = start_idx + page_size
            page_history = current_history[start_idx:end_idx]

            history_msg = "\n".join(
                [f"**{start_idx + i + 1}.** {song}" for i, song in enumerate(page_history)]
            )

            embed = discord.Embed(
                title="Song History",
                description=history_msg or "No entries on this page.",
                color=0x4ecdc4
            )
            embed.add_field(
                name="Info",
                value=f"Page **{page + 1}** of **{total_pages}** • Total: **{len(current_history)}**",
                inline=True
            )
            if interaction.guild and interaction.guild.icon:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            embed.set_footer(text="Use the buttons to navigate pages.")
            embed.timestamp = discord.utils.utcnow()
            return embed

        view = self.HistoryView(self.bot, current_history, create_history_embed, total_pages)
        embed = create_history_embed(0)
        await interaction.followup.send(embed=embed, view=view)

    class HistoryView(View):
        def __init__(self, bot, history, embed_func, total_pages: int):
            super().__init__(timeout=300)
            self.bot = bot
            self.history = history
            self.embed_func = embed_func
            self.total_pages = total_pages
            self.current_page = 0

            self.prev_btn = Button(emoji="⬅️", style=SECONDARY, disabled=True, label="Previous", row=0)
            self.prev_btn.callback = self.prev_page
            self.add_item(self.prev_btn)

            self.next_btn = Button(
                emoji="➡️",
                style=SECONDARY,
                disabled=(total_pages <= 1),
                label="Next",
                row=0
            )
            self.next_btn.callback = self.next_page
            self.add_item(self.next_btn)

        async def prev_page(self, interaction: discord.Interaction):
            if self.current_page > 0:
                self.current_page -= 1
                embed = self.embed_func(self.current_page)
                self.prev_btn.disabled = (self.current_page == 0)
                self.next_btn.disabled = (self.current_page >= self.total_pages - 1)
                await interaction.response.edit_message(embed=embed, view=self)

        async def next_page(self, interaction: discord.Interaction):
            if self.current_page < self.total_pages - 1:
                self.current_page += 1
                embed = self.embed_func(self.current_page)
                self.prev_btn.disabled = (self.current_page == 0)
                self.next_btn.disabled = (self.current_page >= self.total_pages - 1)
                await interaction.response.edit_message(embed=embed, view=self)


class LyricsView(View):
    """Paginated lyrics display view."""

    def __init__(self, lyrics_data: dict, lines_per_page: int = 20):
        super().__init__(timeout=300)
        self.lyrics_data = lyrics_data
        self.lines = lyrics_data['plain_lyrics'].splitlines()
        self.lines_per_page = lines_per_page
        self.total_pages = max(1, (len(self.lines) + lines_per_page - 1) // lines_per_page)
        self.current_page = 0

        self.prev_btn = Button(emoji="⬅️", style=SECONDARY, disabled=True, label="Previous", row=1)
        self.prev_btn.callback = self._prev_page
        self.add_item(self.prev_btn)

        self.next_btn = Button(
            emoji="➡️", style=SECONDARY,
            disabled=(self.total_pages <= 1),
            label="Next", row=1
        )
        self.next_btn.callback = self._next_page
        self.add_item(self.next_btn)

        self.close_btn = Button(emoji="✖️", style=DANGER, label="Close", row=1)
        self.close_btn.callback = self._close
        self.add_item(self.close_btn)

    def create_page_embed(self, page: int = 0) -> discord.Embed:
        start = page * self.lines_per_page
        end = start + self.lines_per_page
        page_text = "\n".join(self.lines[start:end])
        if not page_text.strip():
            page_text = "*(empty lines)*"

        embed = discord.Embed(
            title=f"🎤 {self.lyrics_data['track_name']}",
            description=f"**{self.lyrics_data['artist_name']}**\n\n{page_text}",
            color=0x9b59b6
        )
        embed.set_footer(text=f"Page {page + 1}/{self.total_pages} • Lyrics via lrclib.net")
        embed.timestamp = discord.utils.utcnow()
        return embed

    async def _prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            self.prev_btn.disabled = (self.current_page == 0)
            self.next_btn.disabled = (self.current_page >= self.total_pages - 1)
            await interaction.response.edit_message(
                embed=self.create_page_embed(self.current_page), view=self
            )

    async def _next_page(self, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.prev_btn.disabled = (self.current_page == 0)
            self.next_btn.disabled = (self.current_page >= self.total_pages - 1)
            await interaction.response.edit_message(
                embed=self.create_page_embed(self.current_page), view=self
            )

    async def _close(self, interaction: discord.Interaction):
        try:
            await interaction.message.delete()
        except Exception:
            pass
        self.stop()


class LyricsButtonView(View):
    """View with a Lyrics button, attached to Now Playing embeds."""

    def __init__(self, music_cog, song_title: str, song_artist: str):
        super().__init__(timeout=600)
        self.music_cog = music_cog
        self.song_title = song_title
        self.song_artist = song_artist

    @discord.ui.button(label="Lyrics", emoji="🎤", style=PURPLE)
    async def lyrics_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        await interaction.response.edit_message(view=self)

        loading_embed = discord.Embed(
            title="🎤 Fetching Lyrics...",
            description=f"Searching for lyrics for **{self.song_title}**...",
            color=0x3498db
        )
        await interaction.followup.send(embed=loading_embed, ephemeral=True)

        lyrics_data = await fetch_lyrics(self.song_title, self.song_artist)

        if not lyrics_data:
            not_found = discord.Embed(
                title="🎤 Lyrics Not Found",
                description=f"Could not find lyrics for **{self.song_title}**.",
                color=0xe74c3c
            )
            try:
                await interaction.edit_original_response(embed=not_found)
            except Exception:
                pass
            return

        view = LyricsView(lyrics_data)
        embed = view.create_page_embed(0)
        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class QueueView(View):
    """Paginated queue display view."""

    def __init__(self, queue_items: list, make_embed_fn, items_per_page: int = 10):
        super().__init__(timeout=300)
        self.queue_items = queue_items
        self.make_embed_fn = make_embed_fn
        self.items_per_page = items_per_page
        self.total_pages = max(1, (len(queue_items) + items_per_page - 1) // items_per_page)
        self.current_page = 0

        self.prev_btn = Button(emoji="⬅️", style=SECONDARY, disabled=True, label="Previous", row=1)
        self.prev_btn.callback = self._prev_page
        self.add_item(self.prev_btn)

        self.next_btn = Button(
            emoji="➡️", style=SECONDARY,
            disabled=(self.total_pages <= 1),
            label="Next", row=1
        )
        self.next_btn.callback = self._next_page
        self.add_item(self.next_btn)

    def create_page_embed(self, page: int = 0) -> discord.Embed:
        return self.make_embed_fn(page)

    async def _prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            self.prev_btn.disabled = (self.current_page == 0)
            self.next_btn.disabled = (self.current_page >= self.total_pages - 1)
            await interaction.response.edit_message(
                embed=self.create_page_embed(self.current_page), view=self
            )

    async def _next_page(self, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self.prev_btn.disabled = (self.current_page == 0)
            self.next_btn.disabled = (self.current_page >= self.total_pages - 1)
            await interaction.response.edit_message(
                embed=self.create_page_embed(self.current_page), view=self
            )
