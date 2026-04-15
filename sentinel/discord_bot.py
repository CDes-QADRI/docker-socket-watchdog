"""
Discord Bot Module — Two-Way Interactive Discord Bot.

Provides interactive buttons (Restart / Skip) on crash alerts.
When a user clicks a button in Discord, the bot restarts or skips
the container WITHOUT needing terminal access.

Architecture:
- Runs in a daemon thread alongside the main event listener
- Connects to Discord Gateway via WebSocket (outbound only — no public URL needed)
- Receives INTERACTION_CREATE events when users click buttons
- Executes docker restart/skip commands and updates the Discord message
"""

import asyncio
import threading
import traceback
import docker as docker_sdk
from datetime import datetime, timezone
from sentinel.logger import log

try:
    import discord
    from discord import ButtonStyle, Interaction
    from discord.ui import View, Button
    DISCORD_PY_AVAILABLE = True
except ImportError:
    DISCORD_PY_AVAILABLE = False


# ─── Colors ────────────────────────────────────────────────────────────────────

COLORS = {
    "critical": 0xFF3838,
    "warning": 0xFFB830,
    "success": 0x2ECC71,
    "info": 0x3B82F6,
}

DOCKER_THUMBNAIL = "https://cdn-icons-png.flaticon.com/512/5969/5969059.png"
SHIELD_ICON = "https://cdn-icons-png.flaticon.com/512/6941/6941697.png"


# ─── Restart / Skip View ──────────────────────────────────────────────────────

class ContainerActionView(View):
    """Discord UI View with Restart and Skip buttons for a crashed container."""

    def __init__(self, container_name: str, container_id: str, docker_client,
                 restart_timeout: int = 30):
        # timeout=None → buttons never expire while bot is running
        super().__init__(timeout=None)
        self.container_name = container_name
        self.container_id = container_id
        self.docker_client = docker_client
        self.restart_timeout = restart_timeout

        # Create buttons with unique custom_ids (container name encoded)
        restart_btn = Button(
            style=ButtonStyle.success,
            label="🔄 Restart",
            custom_id=f"dsw_restart_{container_name}",
        )
        restart_btn.callback = self.restart_callback
        self.add_item(restart_btn)

        skip_btn = Button(
            style=ButtonStyle.secondary,
            label="⏭️ Skip",
            custom_id=f"dsw_skip_{container_name}",
        )
        skip_btn.callback = self.skip_callback
        self.add_item(skip_btn)

    def _blocking_restart(self):
        """Perform blocking Docker restart in thread pool (avoids blocking event loop)."""
        container = self.docker_client.containers.get(self.container_name)
        container.restart(timeout=self.restart_timeout)
        container.reload()
        return container.status

    async def restart_callback(self, interaction: Interaction):
        """Handle the Restart button click."""
        await interaction.response.defer(ephemeral=False)

        user = interaction.user
        log.info(
            f"🔘 Discord user '{user.display_name}' clicked RESTART "
            f"for '{self.container_name}'"
        )

        loop = asyncio.get_running_loop()
        try:
            new_status = await loop.run_in_executor(None, self._blocking_restart)

            if new_status == "running":
                result_embed = discord.Embed(
                    title="✅ Container Restarted via Discord",
                    description=(
                        f"**{self.container_name}** has been restarted successfully "
                        f"by **{user.display_name}**."
                    ),
                    color=COLORS["success"],
                    timestamp=datetime.now(timezone.utc),
                )
                result_embed.add_field(
                    name="📦 Container", value=f"`{self.container_name}`", inline=True
                )
                result_embed.add_field(
                    name="🔄 Status", value="🟢 Running", inline=True
                )
                result_embed.set_footer(
                    text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL
                )
                log.info(f"✅ '{self.container_name}' restarted via Discord by {user.display_name}")
            else:
                result_embed = discord.Embed(
                    title="⚠️ Container Restarted But Not Running",
                    description=(
                        f"**{self.container_name}** was restarted but is now "
                        f"in `{new_status}` state."
                    ),
                    color=COLORS["warning"],
                    timestamp=datetime.now(timezone.utc),
                )
                result_embed.set_footer(
                    text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL
                )
                log.warning(
                    f"⚠️ '{self.container_name}' restarted but status is {new_status}"
                )

        except docker_sdk.errors.NotFound:
            result_embed = discord.Embed(
                title="❌ Container Not Found",
                description=(
                    f"**{self.container_name}** no longer exists. "
                    f"It may have been removed."
                ),
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            result_embed.set_footer(
                text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL
            )
            log.error(f"Container '{self.container_name}' not found when restart clicked")

        except Exception as e:
            result_embed = discord.Embed(
                title="❌ Restart Failed",
                description=(
                    f"Failed to restart **{self.container_name}**: `{str(e)[:200]}`"
                ),
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            result_embed.set_footer(
                text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL
            )
            log.error(f"Error restarting '{self.container_name}' via Discord: {e}")

        for item in self.children:
            item.disabled = True

        try:
            await interaction.message.edit(view=self)
            await interaction.followup.send(embed=result_embed)
        except Exception as e:
            log.error(f"Failed to update Discord message after restart: {e}")

    async def skip_callback(self, interaction: Interaction):
        """Handle the Skip button click."""
        user = interaction.user
        log.info(
            f"🔘 Discord user '{user.display_name}' clicked SKIP "
            f"for '{self.container_name}'"
        )

        result_embed = discord.Embed(
            title="⏭️ Restart Skipped via Discord",
            description=(
                f"**{user.display_name}** chose to skip restarting "
                f"**{self.container_name}**."
            ),
            color=COLORS["info"],
            timestamp=datetime.now(timezone.utc),
        )
        result_embed.set_footer(
            text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL
        )

        for item in self.children:
            item.disabled = True

        try:
            await interaction.response.send_message(embed=result_embed)
            await interaction.message.edit(view=self)
        except Exception as e:
            log.error(f"Failed to update Discord message after skip: {e}")


# ─── Sentinel Discord Bot ────────────────────────────────────────────────────

class SentinelBot(discord.Client):
    """
    A lightweight Discord bot that:
    1. Sends crash alerts WITH interactive buttons
    2. Receives button clicks and executes restart/skip
    """

    def __init__(self, bot_token: str, channel_id: int, docker_client,
                 config=None):
        intents = discord.Intents.default()
        super().__init__(intents=intents)

        self.bot_token = bot_token
        self.channel_id = channel_id
        self.docker_client = docker_client
        self.config = config
        self._ready = threading.Event()
        self._loop = None
        self._startup_error = None

    async def on_ready(self):
        """Called when the bot successfully connects to Discord."""
        log.info(
            f"🤖 Discord Bot connected as '{self.user.display_name}' "
            f"(ID: {self.user.id})"
        )
        log.info(f"🔗 Bot will send interactive alerts to channel ID: {self.channel_id}")
        self._ready.set()

    async def on_error(self, event_method, *args, **kwargs):
        """Handle unhandled exceptions in bot event handlers."""
        log.error(f"Discord bot error in {event_method}: {traceback.format_exc()}")

    async def _send_interactive_alert(self, event):
        """
        Send a crash/issue alert WITH Restart/Skip buttons to Discord.
        Called from the events thread via thread-safe scheduling.
        """
        channel = self.get_channel(self.channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception as e:
                raise RuntimeError(f"Cannot access channel {self.channel_id}: {e}")

        severity = event.severity
        color = COLORS.get(severity, COLORS["info"])

        embed = discord.Embed(
            title=f"{event.emoji} {event.description}",
            description=f"**{event.container_name}** → `{event.action}`",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        embed.set_thumbnail(url=DOCKER_THUMBNAIL)
        embed.add_field(name="📦 Container", value=f"`{event.container_name}`", inline=True)
        embed.add_field(name="🏷️ Image", value=f"`{event.image}`", inline=True)
        embed.add_field(
            name="🔢 Exit Code",
            value=f"`{event.exit_code}`" if event.exit_code else "`N/A`",
            inline=True,
        )
        embed.add_field(name="🆔 Container ID", value=f"`{event.container_id}`", inline=True)
        embed.add_field(
            name="⏰ Detected At",
            value=f"<t:{int(event.timestamp.timestamp())}:T>",
            inline=True,
        )

        if event.needs_attention:
            embed.add_field(
                name="🎯 Action",
                value="👇 **Click a button below** to restart or skip this container.",
                inline=False,
            )

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)

        # Create the view with buttons (only for events needing attention)
        if event.needs_attention:
            timeout = self.config.restart_timeout if self.config else 30
            view = ContainerActionView(
                container_name=event.container_name,
                container_id=event.container_id,
                docker_client=self.docker_client,
                restart_timeout=timeout,
            )
            await channel.send(embed=embed, view=view)
        else:
            # Informational events — no buttons needed
            await channel.send(embed=embed)

    def send_interactive_alert(self, event):
        """
        Thread-safe method to send an interactive alert.
        Can be called from any thread — it schedules the coroutine
        on the bot's async event loop.
        """
        if not self._ready.wait(timeout=15):
            log.warning("Bot not ready after 15s — falling back to webhook")
            return False

        if self._startup_error:
            return False

        if not self._loop or self._loop.is_closed():
            log.warning("Bot event loop not available — falling back to webhook")
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._send_interactive_alert(event), self._loop
        )

        try:
            future.result(timeout=15)
            return True
        except Exception as e:
            log.error(f"Failed to send interactive alert: {e}")
            return False

    def run_in_thread(self):
        """
        Start the bot in a new daemon thread.
        Returns the thread object.
        """
        def _run():
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self.start(self.bot_token))
            except Exception as e:
                log.error(f"Discord bot crashed: {e}")
                self._startup_error = e
                self._ready.set()  # Unblock anyone waiting

        thread = threading.Thread(target=_run, daemon=True, name="DiscordBot")
        thread.start()
        return thread

    async def shutdown(self):
        """Graceful shutdown."""
        await self.close()


# ─── Helper: Check if bot is available ────────────────────────────────────────

def is_bot_available() -> bool:
    """Check if discord.py is installed."""
    return DISCORD_PY_AVAILABLE
