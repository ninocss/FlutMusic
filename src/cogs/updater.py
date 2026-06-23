# ruff: noqa: F403 F405
import asyncio
from discord.ext import commands
from util.constants import *
import subprocess
import os
import tempfile
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

SOURCES = {
    "beta": {
        "url": "https://github.com/OseMine/FlutMusic.git",
        "branch": "feature/platform-resolver",
        "label": "Beta (platform-resolver)",
    },
    "full": {
        "url": "https://github.com/ninocss/FlutMusic.git",
        "branch": "main",
        "label": "Full (main)",
    },
}

RESTART_SCRIPT_NAME = "flutmusic_restart.bat"


class UpdaterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="update")
    @commands.has_permissions(administrator=True)
    async def update(self, ctx, source: str = "beta"):
        """Update the bot from GitHub. Usage: !update [beta|full]"""
        source = source.lower()
        if source not in SOURCES:
            await ctx.send(f"Invalid source. Use `beta` or `full`.")
            return

        info = SOURCES[source]

        has_git = False
        try:
            subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=True)
            has_git = True
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        if not has_git:
            await ctx.send("Git is not installed or not in PATH.")
            return

        await ctx.send(f"Updating from **{info['label']}**...")

        remote_name = f"updater_{source}"

        def do_git_update():
            subprocess.run(
                ["git", "remote", "remove", remote_name],
                capture_output=True, timeout=15
            )

            result = subprocess.run(
                ["git", "remote", "add", remote_name, info["url"]],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to add remote: {result.stderr.strip()}")

            result = subprocess.run(
                ["git", "fetch", "--quiet", remote_name],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to fetch: {result.stderr.strip()}")

            target = f"{remote_name}/{info['branch']}"
            result = subprocess.run(
                ["git", "reset", "--hard", target],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to reset: {result.stderr.strip()}")

            subprocess.run(
                ["git", "clean", "-fd"],
                capture_output=True, timeout=30
            )

            result = subprocess.run(
                ["pip", "install", "-r", "requirements.txt"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                logger.warning(f"pip install warnings:\n{result.stderr.strip()}")

            subprocess.run(
                ["git", "remote", "remove", remote_name],
                capture_output=True, timeout=15
            )

            return True

        try:
            await asyncio.to_thread(do_git_update)
        except Exception as e:
            await ctx.send(f"Update failed: {e}")
            logger.error(f"Update failed: {e}")
            return

        await ctx.send("Update complete! Restarting...")
        logger.info(f"Update to {source} complete, restarting...")

        script = f"""@echo off
timeout /t 2 /nobreak >nul
cd /d "{os.getcwd()}"
python -m src.main
del "%~f0"
"""
        script_path = os.path.join(tempfile.gettempdir(), RESTART_SCRIPT_NAME)
        with open(script_path, "w") as f:
            f.write(script)

        subprocess.Popen(
            ["cmd.exe", "/c", script_path],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True
        )

        await self.bot.close()
