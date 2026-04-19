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
from sentinel.sanitizer import sanitize
from sentinel.alerter import rate_limiter

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

# Valid container name pattern (Docker allows [a-zA-Z0-9][a-zA-Z0-9_.-]*)
import re
_VALID_CONTAINER_NAME = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.\-]{0,127}$')


def _is_valid_container_name(name: str) -> bool:
    """Validate that a container name matches Docker's naming rules."""
    return bool(name and _VALID_CONTAINER_NAME.match(name))


async def _check_authorization(interaction: Interaction, authorized_role_ids: list) -> bool:
    """
    Check if the user clicking a button is authorized.
    Returns True if authorized, False if denied (sends ephemeral denial message).
    """
    if not authorized_role_ids:
        return True  # No roles configured = allow everyone

    # Server admins always allowed
    if interaction.user.guild_permissions.administrator:
        return True

    user_role_ids = {role.id for role in interaction.user.roles}
    if user_role_ids & set(authorized_role_ids):
        return True

    # Deny
    try:
        await interaction.response.send_message(
            "🔒 **Access Denied** — You don't have permission to manage containers.\n"
            "Ask a server admin to add your role to `authorized_role_ids` in config.yaml.",
            ephemeral=True,
        )
    except Exception:
        pass
    log.warning(
        f"🔒 Unauthorized button click by '{interaction.user.display_name}' "
        f"(ID: {interaction.user.id})"
    )
    return False


# ─── Restart / Skip View ──────────────────────────────────────────────────────

class ContainerActionView(View):
    """Discord UI View with Restart and Skip buttons for a crashed container."""

    def __init__(self, container_name: str, container_id: str, docker_client,
                 restart_timeout: int = 30, authorized_role_ids: list = None):
        # timeout=None → buttons never expire while bot is running
        super().__init__(timeout=None)
        self.container_name = container_name
        self.container_id = container_id
        self.docker_client = docker_client
        self.restart_timeout = restart_timeout
        self.authorized_role_ids = authorized_role_ids or []

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
        if not await _check_authorization(interaction, self.authorized_role_ids):
            return

        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            log.error(f"Failed to defer restart interaction: {e}")
            return

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
                    f"Failed to restart **{self.container_name}**. Check server logs for details."
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
            try:
                await interaction.followup.send(
                    content=f"Action completed but failed to update message: {e}"
                )
            except Exception:
                pass

    async def skip_callback(self, interaction: Interaction):
        """Handle the Skip button click."""
        if not await _check_authorization(interaction, self.authorized_role_ids):
            return

        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            log.error(f"Failed to defer skip interaction: {e}")
            return

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
            await interaction.message.edit(view=self)
            await interaction.followup.send(embed=result_embed)
        except Exception as e:
            log.error(f"Failed to update Discord message after skip: {e}")
            try:
                await interaction.followup.send(
                    content=f"Skip registered but failed to update message: {e}"
                )
            except Exception:
                pass


# ─── Dashboard View (Docker Management) ───────────────────────────────────────

class DashboardView(View):
    """Dashboard with Docker management buttons — control everything from Discord."""

    def __init__(self, docker_client, config=None):
        super().__init__(timeout=None)
        self.docker_client = docker_client
        self.config = config
        self.authorized_role_ids = config.authorized_role_ids if config else []

        # Dashboard action buttons
        refresh_btn = Button(
            style=ButtonStyle.primary,
            label="🔄 Refresh",
            custom_id="dsw_dashboard_refresh",
        )
        refresh_btn.callback = self.refresh_callback
        self.add_item(refresh_btn)

        start_all_btn = Button(
            style=ButtonStyle.success,
            label="▶️ Start All Stopped",
            custom_id="dsw_dashboard_start_all",
        )
        start_all_btn.callback = self.start_all_callback
        self.add_item(start_all_btn)

        stop_all_btn = Button(
            style=ButtonStyle.danger,
            label="⏹️ Stop All Running",
            custom_id="dsw_dashboard_stop_all",
        )
        stop_all_btn.callback = self.stop_all_callback
        self.add_item(stop_all_btn)

        restart_all_btn = Button(
            style=ButtonStyle.primary,
            label="🔁 Restart All",
            custom_id="dsw_dashboard_restart_all",
        )
        restart_all_btn.callback = self.restart_all_callback
        self.add_item(restart_all_btn)

        list_btn = Button(
            style=ButtonStyle.secondary,
            label="📋 Container List",
            custom_id="dsw_dashboard_list",
        )
        list_btn.callback = self.list_callback
        self.add_item(list_btn)

    def _get_all_containers(self):
        """Get all containers with their details."""
        containers = self.docker_client.containers.list(all=True)
        result = []
        for c in containers:
            try:
                state = c.attrs.get("State", {})
                health = state.get("Health", {}).get("Status", "")
                image = str(c.image.tags[0]) if c.image.tags else "unknown"
                result.append({
                    "name": c.name,
                    "status": c.status,
                    "health": health,
                    "image": image,
                    "id": c.short_id,
                })
            except Exception:
                result.append({
                    "name": c.name,
                    "status": c.status,
                    "health": "",
                    "image": "unknown",
                    "id": c.short_id,
                })
        return result

    async def refresh_callback(self, interaction: Interaction):
        """Refresh the dashboard with current container states."""
        if not await _check_authorization(interaction, self.authorized_role_ids):
            return
        await interaction.response.defer(ephemeral=False)

        loop = asyncio.get_running_loop()
        containers = await loop.run_in_executor(None, self._get_all_containers)

        running = [c for c in containers if c["status"] == "running"]
        stopped = [c for c in containers if c["status"] != "running"]
        total = len(containers)

        embed = discord.Embed(
            title="📊 Container Dashboard — Refreshed",
            description=(
                f"**{total}** total containers — "
                f"**{len(running)}** running, **{len(stopped)}** stopped"
            ),
            color=COLORS["success"] if not stopped else COLORS["warning"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=DOCKER_THUMBNAIL)

        if running:
            lines = []
            for c in running:
                health_icon = "💚" if c["health"] == "healthy" else ""
                lines.append(f"🟢 **{c['name']}** {health_icon}\n╰ `{c['image']}` • `{c['id']}`")
            text = "\n".join(lines)
            if len(text) <= 1024:
                embed.add_field(name=f"✅ Running ({len(running)})", value=text, inline=False)
            else:
                for i in range(0, len(lines), 8):
                    chunk = lines[i:i+8]
                    embed.add_field(
                        name=f"✅ Running (Part {i//8+1})",
                        value="\n".join(chunk),
                        inline=False,
                    )

        if stopped:
            lines = []
            for c in stopped:
                status_icon = "🔴" if c["status"] == "exited" else "💀" if c["status"] == "dead" else "⚪"
                lines.append(f"{status_icon} **{c['name']}** — `{c['status']}`\n╰ `{c['image']}` • `{c['id']}`")
            text = "\n".join(lines)
            if len(text) <= 1024:
                embed.add_field(name=f"🛑 Stopped ({len(stopped)})", value=text, inline=False)
            else:
                for i in range(0, len(lines), 8):
                    chunk = lines[i:i+8]
                    embed.add_field(
                        name=f"🛑 Stopped (Part {i//8+1})",
                        value="\n".join(chunk),
                        inline=False,
                    )

        embed.set_footer(text="docker-socket-watchdog • Dashboard", icon_url=DOCKER_THUMBNAIL)

        view = DashboardView(self.docker_client, self.config)
        await interaction.followup.send(embed=embed, view=view)

    async def start_all_callback(self, interaction: Interaction):
        """Start all stopped containers."""
        if not await _check_authorization(interaction, self.authorized_role_ids):
            return
        await interaction.response.defer(ephemeral=False)

        loop = asyncio.get_running_loop()

        def _start_all():
            results = []
            stopped = self.docker_client.containers.list(
                all=True, filters={"status": "exited"}
            )
            for c in stopped:
                try:
                    c.start()
                    results.append(f"✅ **{c.name}** — started")
                except Exception as e:
                    results.append(f"❌ **{c.name}** — {str(e)[:100]}")
            return results

        results = await loop.run_in_executor(None, _start_all)

        if not results:
            embed = discord.Embed(
                title="ℹ️ No Stopped Containers",
                description="All containers are already running.",
                color=COLORS["info"],
                timestamp=datetime.now(timezone.utc),
            )
        else:
            embed = discord.Embed(
                title="▶️ Start All — Results",
                description="\n".join(results),
                color=COLORS["success"],
                timestamp=datetime.now(timezone.utc),
            )

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
        await interaction.followup.send(embed=embed)

    async def stop_all_callback(self, interaction: Interaction):
        """Stop all running containers."""
        if not await _check_authorization(interaction, self.authorized_role_ids):
            return
        await interaction.response.defer(ephemeral=False)

        loop = asyncio.get_running_loop()

        def _stop_all():
            results = []
            running = self.docker_client.containers.list(filters={"status": "running"})
            for c in running:
                try:
                    c.stop(timeout=10)
                    results.append(f"⏹️ **{c.name}** — stopped")
                except Exception as e:
                    results.append(f"❌ **{c.name}** — {str(e)[:100]}")
            return results

        results = await loop.run_in_executor(None, _stop_all)

        if not results:
            embed = discord.Embed(
                title="ℹ️ No Running Containers",
                description="All containers are already stopped.",
                color=COLORS["info"],
                timestamp=datetime.now(timezone.utc),
            )
        else:
            embed = discord.Embed(
                title="⏹️ Stop All — Results",
                description="\n".join(results),
                color=COLORS["warning"],
                timestamp=datetime.now(timezone.utc),
            )

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
        await interaction.followup.send(embed=embed)

    async def restart_all_callback(self, interaction: Interaction):
        """Restart all running containers."""
        if not await _check_authorization(interaction, self.authorized_role_ids):
            return
        await interaction.response.defer(ephemeral=False)

        loop = asyncio.get_running_loop()

        def _restart_all():
            results = []
            running = self.docker_client.containers.list(filters={"status": "running"})
            for c in running:
                try:
                    c.restart(timeout=10)
                    results.append(f"🔁 **{c.name}** — restarted")
                except Exception as e:
                    results.append(f"❌ **{c.name}** — {str(e)[:100]}")
            return results

        results = await loop.run_in_executor(None, _restart_all)

        if not results:
            embed = discord.Embed(
                title="ℹ️ No Running Containers",
                description="No running containers to restart.",
                color=COLORS["info"],
                timestamp=datetime.now(timezone.utc),
            )
        else:
            embed = discord.Embed(
                title="🔁 Restart All — Results",
                description="\n".join(results),
                color=COLORS["success"],
                timestamp=datetime.now(timezone.utc),
            )

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
        await interaction.followup.send(embed=embed)

    async def list_callback(self, interaction: Interaction):
        """Show detailed container list with per-container action buttons."""
        if not await _check_authorization(interaction, self.authorized_role_ids):
            return
        await interaction.response.defer(ephemeral=False)

        loop = asyncio.get_running_loop()
        containers = await loop.run_in_executor(None, self._get_all_containers)

        # Send per-container cards with action buttons (max 5 per message due to Discord limits)
        for i in range(0, len(containers), 5):
            batch = containers[i:i+5]
            embed = discord.Embed(
                title=f"📋 Container List ({i+1}–{min(i+5, len(containers))} of {len(containers)})",
                color=COLORS["info"],
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_thumbnail(url=DOCKER_THUMBNAIL)

            view = View(timeout=None)
            for c in batch:
                status_emoji = "🟢" if c["status"] == "running" else "🔴"
                embed.add_field(
                    name=f"{status_emoji} {c['name']}",
                    value=(
                        f"**Status:** `{c['status']}`\n"
                        f"**Image:** `{c['image']}`\n"
                        f"**ID:** `{c['id']}`"
                    ),
                    inline=True,
                )

                # Add per-container action button
                if c["status"] == "running":
                    btn = Button(
                        style=ButtonStyle.danger,
                        label=f"⏹ {c['name'][:20]}",
                        custom_id=f"dsw_stop_{c['name']}",
                    )
                else:
                    btn = Button(
                        style=ButtonStyle.success,
                        label=f"▶ {c['name'][:20]}",
                        custom_id=f"dsw_start_{c['name']}",
                    )
                view.add_item(btn)

            embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
            await interaction.followup.send(embed=embed, view=view)


# ─── Per-Container Management View ─────────────────────────────────────────────

class ContainerManageView(View):
    """Per-container action buttons: Start/Stop, Restart, Logs, Inspect."""

    def __init__(self, container_name, docker_client, config=None):
        super().__init__(timeout=None)
        self.container_name = container_name
        self.docker_client = docker_client
        self.config = config

        # Add all management buttons
        restart_btn = Button(
            style=ButtonStyle.success,
            label="🔄 Restart",
            custom_id=f"dsw_restart_{container_name}",
        )
        self.add_item(restart_btn)

        stop_btn = Button(
            style=ButtonStyle.danger,
            label="⏹️ Stop",
            custom_id=f"dsw_stop_{container_name}",
        )
        self.add_item(stop_btn)

        start_btn = Button(
            style=ButtonStyle.success,
            label="▶️ Start",
            custom_id=f"dsw_start_{container_name}",
        )
        self.add_item(start_btn)

        logs_btn = Button(
            style=ButtonStyle.primary,
            label="📜 Logs",
            custom_id=f"dsw_logs_{container_name}",
        )
        self.add_item(logs_btn)

        inspect_btn = Button(
            style=ButtonStyle.secondary,
            label="🔍 Inspect",
            custom_id=f"dsw_inspect_{container_name}",
        )
        self.add_item(inspect_btn)


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
        self._bot_ready = threading.Event()
        self._loop = None
        self._startup_error = None

    async def on_ready(self):
        """Called when the bot successfully connects to Discord."""
        log.info(
            f"🤖 Discord Bot connected as '{self.user.display_name}' "
            f"(ID: {self.user.id})"
        )
        log.info(f"🔗 Bot will send interactive alerts to channel ID: {self.channel_id}")
        self._bot_ready.set()

    async def setup_hook(self):
        """Called during bot startup."""
        log.info("🔧 Bot setup_hook — interaction handler ready for cross-session buttons")

    async def on_interaction(self, interaction: Interaction):
        """
        Handle button clicks on messages from PREVIOUS bot sessions.

        discord.py's on_interaction fires AFTER View dispatch. If the View
        is still tracked in memory (current session), View callbacks handle
        the interaction and this method does nothing (is_done check).
        For old messages where no View is tracked, we handle them here.
        """
        # Only handle component interactions (buttons)
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data.get("custom_id", "")

        # Check if this is one of our buttons
        if not custom_id.startswith("dsw_"):
            return

        # discord.py calls on_interaction AFTER trying to dispatch to
        # tracked Views. If a tracked View handled it, response is done.
        if interaction.response.is_done():
            return

        # Authorization check
        authorized_roles = self.config.authorized_role_ids if self.config else []
        if not await _check_authorization(interaction, authorized_roles):
            return

        # Extract container name from custom_id and validate
        if custom_id.startswith("dsw_restart_"):
            container_name = custom_id[len("dsw_restart_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_persistent_restart(interaction, container_name)
        elif custom_id.startswith("dsw_skip_"):
            container_name = custom_id[len("dsw_skip_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_persistent_skip(interaction, container_name)
        elif custom_id.startswith("dsw_start_"):
            container_name = custom_id[len("dsw_start_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_start(interaction, container_name)
        elif custom_id.startswith("dsw_stop_"):
            container_name = custom_id[len("dsw_stop_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_stop(interaction, container_name)
        elif custom_id.startswith("dsw_logs_"):
            container_name = custom_id[len("dsw_logs_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_logs(interaction, container_name)
        elif custom_id.startswith("dsw_inspect_"):
            container_name = custom_id[len("dsw_inspect_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_inspect(interaction, container_name)
        elif custom_id.startswith("dsw_dashboard_"):
            # Dashboard buttons — create a new DashboardView and dispatch
            dashboard_view = DashboardView(self.docker_client, self.config)
            action = custom_id[len("dsw_dashboard_"):]
            handler = {
                "refresh": dashboard_view.refresh_callback,
                "start_all": dashboard_view.start_all_callback,
                "stop_all": dashboard_view.stop_all_callback,
                "restart_all": dashboard_view.restart_all_callback,
                "list": dashboard_view.list_callback,
            }.get(action)
            if handler:
                await handler(interaction)
        elif custom_id.startswith("dsw_start_"):
            container_name = custom_id[len("dsw_start_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_start(interaction, container_name)
        elif custom_id.startswith("dsw_stop_"):
            container_name = custom_id[len("dsw_stop_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_stop(interaction, container_name)
        elif custom_id.startswith("dsw_logs_"):
            container_name = custom_id[len("dsw_logs_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_logs(interaction, container_name)
        elif custom_id.startswith("dsw_inspect_"):
            container_name = custom_id[len("dsw_inspect_"):]
            if not _is_valid_container_name(container_name):
                log.warning(f"🔒 Rejected invalid container name in custom_id: {custom_id[:60]}")
                return
            await self._handle_container_inspect(interaction, container_name)
        elif custom_id.startswith("dsw_dashboard_"):
            # Dashboard buttons — create a new DashboardView and dispatch
            dashboard_view = DashboardView(self.docker_client, self.config)
            action = custom_id[len("dsw_dashboard_"):]
            handler = {
                "refresh": dashboard_view.refresh_callback,
                "start_all": dashboard_view.start_all_callback,
                "stop_all": dashboard_view.stop_all_callback,
                "restart_all": dashboard_view.restart_all_callback,
                "list": dashboard_view.list_callback,
            }.get(action)
            if handler:
                await handler(interaction)

    async def _handle_persistent_restart(self, interaction: Interaction, container_name: str):
        """Handle restart button click from a previous bot session's message."""
        try:
            await interaction.response.defer(ephemeral=False)
        except discord.errors.HTTPException as e:
            if e.code == 40060:
                # Already acknowledged by a tracked View — skip
                return
            log.error(f"Failed to defer persistent restart: {e}")
            return
        except Exception as e:
            log.error(f"Failed to defer persistent restart: {e}")
            return

        user = interaction.user
        log.info(f"🔘 Discord user '{user.display_name}' clicked RESTART for '{container_name}' (persistent)")

        loop = asyncio.get_running_loop()
        try:
            def _do_restart():
                container = self.docker_client.containers.get(container_name)
                timeout = self.config.restart_timeout if self.config else 30
                container.restart(timeout=timeout)
                container.reload()
                return container.status

            new_status = await loop.run_in_executor(None, _do_restart)

            if new_status == "running":
                result_embed = discord.Embed(
                    title="✅ Container Restarted via Discord",
                    description=(
                        f"**{container_name}** has been restarted successfully "
                        f"by **{user.display_name}**."
                    ),
                    color=COLORS["success"],
                    timestamp=datetime.now(timezone.utc),
                )
                result_embed.add_field(name="📦 Container", value=f"`{container_name}`", inline=True)
                result_embed.add_field(name="🔄 Status", value="🟢 Running", inline=True)
                log.info(f"✅ '{container_name}' restarted via Discord by {user.display_name} (persistent)")
            else:
                result_embed = discord.Embed(
                    title="⚠️ Container Restarted But Not Running",
                    description=f"**{container_name}** is now in `{new_status}` state.",
                    color=COLORS["warning"],
                    timestamp=datetime.now(timezone.utc),
                )

        except docker_sdk.errors.NotFound:
            result_embed = discord.Embed(
                title="❌ Container Not Found",
                description=f"**{container_name}** no longer exists.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )

        except Exception as e:
            result_embed = discord.Embed(
                title="❌ Restart Failed",
                description=f"Failed to restart **{container_name}**. Check server logs for details.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            log.error(f"Error restarting '{container_name}' via Discord (persistent): {e}")

        result_embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)

        # Disable buttons on the original message
        try:
            view = View()
            for child in (interaction.message.components or []):
                for component in child.children:
                    btn = Button(
                        style=component.style,
                        label=component.label,
                        custom_id=component.custom_id,
                        disabled=True,
                    )
                    view.add_item(btn)
            await interaction.message.edit(view=view)
        except Exception as e:
            log.debug(f"Could not disable old buttons: {e}")

        try:
            await interaction.followup.send(embed=result_embed)
        except Exception as e:
            log.error(f"Failed to send restart result: {e}")

    async def _handle_persistent_skip(self, interaction: Interaction, container_name: str):
        """Handle skip button click from a previous bot session's message."""
        try:
            await interaction.response.defer(ephemeral=False)
        except discord.errors.HTTPException as e:
            if e.code == 40060:
                # Already acknowledged by a tracked View — skip
                return
            log.error(f"Failed to defer persistent skip: {e}")
            return
        except Exception as e:
            log.error(f"Failed to defer persistent skip: {e}")
            return

        user = interaction.user
        log.info(f"🔘 Discord user '{user.display_name}' clicked SKIP for '{container_name}' (persistent)")

        result_embed = discord.Embed(
            title="⏭️ Restart Skipped via Discord",
            description=(
                f"**{user.display_name}** chose to skip restarting "
                f"**{container_name}**."
            ),
            color=COLORS["info"],
            timestamp=datetime.now(timezone.utc),
        )
        result_embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)

        # Disable buttons on the original message
        try:
            view = View()
            for child in (interaction.message.components or []):
                for component in child.children:
                    btn = Button(
                        style=component.style,
                        label=component.label,
                        custom_id=component.custom_id,
                        disabled=True,
                    )
                    view.add_item(btn)
            await interaction.message.edit(view=view)
        except Exception as e:
            log.debug(f"Could not disable old buttons: {e}")

        try:
            await interaction.followup.send(embed=result_embed)
        except Exception as e:
            log.error(f"Failed to send skip result: {e}")

    async def _handle_container_start(self, interaction: Interaction, container_name: str):
        """Handle start button click for a stopped container."""
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            log.error(f"Failed to defer start interaction: {e}")
            return

        user = interaction.user
        log.info(f"🔘 Discord user '{user.display_name}' clicked START for '{container_name}'")

        loop = asyncio.get_running_loop()
        try:
            def _do_start():
                container = self.docker_client.containers.get(container_name)
                container.start()
                container.reload()
                return container.status

            new_status = await loop.run_in_executor(None, _do_start)

            if new_status == "running":
                embed = discord.Embed(
                    title="▶️ Container Started",
                    description=f"**{container_name}** started by **{user.display_name}**",
                    color=COLORS["success"],
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="📦 Container", value=f"`{container_name}`", inline=True)
                embed.add_field(name="🔄 Status", value="🟢 Running", inline=True)
            else:
                embed = discord.Embed(
                    title="⚠️ Container Started But Not Running",
                    description=f"**{container_name}** is in `{new_status}` state.",
                    color=COLORS["warning"],
                    timestamp=datetime.now(timezone.utc),
                )
        except docker_sdk.errors.NotFound:
            embed = discord.Embed(
                title="❌ Container Not Found",
                description=f"**{container_name}** no longer exists.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            embed = discord.Embed(
                title="❌ Start Failed",
                description=f"Failed to start **{container_name}**.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            log.error(f"Error starting '{container_name}' via Discord: {e}")

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
        try:
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.error(f"Failed to send start result: {e}")

    async def _handle_container_stop(self, interaction: Interaction, container_name: str):
        """Handle stop button click for a running container."""
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            log.error(f"Failed to defer stop interaction: {e}")
            return

        user = interaction.user
        log.info(f"🔘 Discord user '{user.display_name}' clicked STOP for '{container_name}'")

        loop = asyncio.get_running_loop()
        try:
            def _do_stop():
                container = self.docker_client.containers.get(container_name)
                container.stop(timeout=10)
                container.reload()
                return container.status

            new_status = await loop.run_in_executor(None, _do_stop)
            embed = discord.Embed(
                title="⏹️ Container Stopped",
                description=f"**{container_name}** stopped by **{user.display_name}**",
                color=COLORS["warning"],
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="📦 Container", value=f"`{container_name}`", inline=True)
            embed.add_field(name="🔄 Status", value=f"🔴 {new_status.title()}", inline=True)
        except docker_sdk.errors.NotFound:
            embed = discord.Embed(
                title="❌ Container Not Found",
                description=f"**{container_name}** no longer exists.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            embed = discord.Embed(
                title="❌ Stop Failed",
                description=f"Failed to stop **{container_name}**.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            log.error(f"Error stopping '{container_name}' via Discord: {e}")

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
        try:
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.error(f"Failed to send stop result: {e}")

    async def _handle_container_logs(self, interaction: Interaction, container_name: str):
        """Fetch and send last 30 lines of container logs."""
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            log.error(f"Failed to defer logs interaction: {e}")
            return

        log.info(f"🔘 Discord user '{interaction.user.display_name}' requested LOGS for '{container_name}'")

        loop = asyncio.get_running_loop()
        try:
            def _get_logs():
                container = self.docker_client.containers.get(container_name)
                logs = container.logs(tail=30, timestamps=True).decode("utf-8", errors="replace")
                return logs

            logs_text = await loop.run_in_executor(None, _get_logs)

            # Truncate to Discord embed limit
            if len(logs_text) > 4000:
                logs_text = logs_text[-4000:]

            embed = discord.Embed(
                title=f"📜 Logs — {container_name}",
                description=f"```\n{sanitize(logs_text) if logs_text.strip() else '(empty)'}\n```",
                color=COLORS["info"],
                timestamp=datetime.now(timezone.utc),
            )
        except docker_sdk.errors.NotFound:
            embed = discord.Embed(
                title="❌ Container Not Found",
                description=f"**{container_name}** no longer exists.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            embed = discord.Embed(
                title="❌ Failed to Fetch Logs",
                description=f"Could not get logs for **{container_name}**.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            log.error(f"Error fetching logs for '{container_name}': {e}")

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
        try:
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.error(f"Failed to send logs: {e}")

    async def _handle_container_inspect(self, interaction: Interaction, container_name: str):
        """Fetch and display container inspection details."""
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception as e:
            log.error(f"Failed to defer inspect interaction: {e}")
            return

        log.info(f"🔘 Discord user '{interaction.user.display_name}' requested INSPECT for '{container_name}'")

        loop = asyncio.get_running_loop()
        try:
            def _inspect():
                container = self.docker_client.containers.get(container_name)
                container.reload()
                attrs = container.attrs
                state = attrs.get("State", {})
                config_data = attrs.get("Config", {})
                network = attrs.get("NetworkSettings", {})
                host_config = attrs.get("HostConfig", {})

                # Extract key info
                ports = network.get("Ports", {})
                port_list = []
                for container_port, bindings in (ports or {}).items():
                    if bindings:
                        for b in bindings:
                            port_list.append(f"{b.get('HostPort', '?')}→{container_port}")
                    else:
                        port_list.append(f"(internal) {container_port}")

                envs = config_data.get("Env", [])
                # Sanitize env vars
                safe_envs = []
                for e in envs[:15]:  # limit to 15
                    safe_envs.append(sanitize(e))

                return {
                    "status": state.get("Status", "unknown"),
                    "started_at": state.get("StartedAt", ""),
                    "finished_at": state.get("FinishedAt", ""),
                    "restart_count": attrs.get("RestartCount", 0),
                    "image": config_data.get("Image", "unknown"),
                    "cmd": " ".join(config_data.get("Cmd", []) or []),
                    "ports": ", ".join(port_list) if port_list else "none",
                    "memory_limit": host_config.get("Memory", 0),
                    "cpu_shares": host_config.get("CpuShares", 0),
                    "env_vars": "\n".join(safe_envs) if safe_envs else "none",
                    "restart_policy": host_config.get("RestartPolicy", {}).get("Name", "no"),
                }

            info = await loop.run_in_executor(None, _inspect)

            embed = discord.Embed(
                title=f"🔍 Inspect — {container_name}",
                color=COLORS["info"],
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_thumbnail(url=DOCKER_THUMBNAIL)
            embed.add_field(name="📊 Status", value=f"`{info['status']}`", inline=True)
            embed.add_field(name="🔄 Restart Policy", value=f"`{info['restart_policy']}`", inline=True)
            embed.add_field(name="🔁 Restarts", value=f"`{info['restart_count']}`", inline=True)
            embed.add_field(name="🏷️ Image", value=f"`{info['image']}`", inline=True)
            embed.add_field(name="🔌 Ports", value=f"`{info['ports']}`", inline=True)
            if info["cmd"]:
                embed.add_field(name="⌨️ Command", value=f"`{info['cmd'][:100]}`", inline=True)
            if info["started_at"] and not info["started_at"].startswith("0001"):
                embed.add_field(name="🕐 Started", value=f"`{info['started_at'][:19]}`", inline=True)
            if info["memory_limit"] > 0:
                embed.add_field(name="🧠 Memory Limit", value=f"`{info['memory_limit'] // 1048576}MB`", inline=True)

            # Add action buttons for this container
            view = ContainerManageView(container_name, self.docker_client, self.config)

        except docker_sdk.errors.NotFound:
            embed = discord.Embed(
                title="❌ Container Not Found",
                description=f"**{container_name}** no longer exists.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            view = None
        except Exception as e:
            embed = discord.Embed(
                title="❌ Inspect Failed",
                description=f"Could not inspect **{container_name}**.",
                color=COLORS["critical"],
                timestamp=datetime.now(timezone.utc),
            )
            view = None
            log.error(f"Error inspecting '{container_name}': {e}")

        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)
        try:
            if view:
                await interaction.followup.send(embed=embed, view=view)
            else:
                await interaction.followup.send(embed=embed)
        except Exception as e:
            log.error(f"Failed to send inspect result: {e}")

    async def on_error(self, event_method, *args, **kwargs):
        """Handle unhandled exceptions in bot event handlers."""
        log.error(f"Discord bot error in {event_method}: {traceback.format_exc()}")

    async def _send_interactive_alert(self, event):
        """
        Send a crash/issue alert WITH Restart/Skip buttons to Discord.
        Called from the events thread via thread-safe scheduling.
        """
        if not rate_limiter.allow(event.container_name):
            log.debug(f"Rate-limited bot alert for '{event.container_name}' — skipping")
            return

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
            authorized = self.config.authorized_role_ids if self.config else []
            view = ContainerActionView(
                container_name=event.container_name,
                container_id=event.container_id,
                docker_client=self.docker_client,
                restart_timeout=timeout,
                authorized_role_ids=authorized,
            )
            await channel.send(embed=embed, view=view)
        else:
            # Informational events — no buttons needed
            await channel.send(embed=embed)

    async def _send_resource_alert(self, alert):
        """
        Send a resource spike alert WITH Restart/Skip buttons to Discord.
        Called from the resource monitor thread via thread-safe scheduling.
        """
        if not rate_limiter.allow(alert.container_name):
            log.debug(f"Rate-limited bot resource alert for '{alert.container_name}' — skipping")
            return

        channel = self.get_channel(self.channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception as e:
                raise RuntimeError(f"Cannot access channel {self.channel_id}: {e}")

        color = COLORS.get(alert.severity, COLORS["warning"])

        if alert.severity == "critical":
            title = f"🚨 CRITICAL — {alert.emoji} Resource Spike!"
            desc = (
                f"**{alert.container_name}** is consuming dangerously high resources.\n"
                f"⚠️ **A crash or OOM kill may be imminent!**"
            )
        else:
            title = f"⚠️ WARNING — {alert.emoji} High Resource Usage"
            desc = (
                f"**{alert.container_name}** is exceeding resource thresholds."
            )

        embed = discord.Embed(
            title=title,
            description=desc,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=DOCKER_THUMBNAIL)
        embed.add_field(
            name="📦 Container", value=f"`{alert.container_name}`", inline=True
        )
        embed.add_field(
            name="🏷️ Image", value=f"`{alert.image}`", inline=True
        )
        embed.add_field(
            name="🧠 RAM",
            value=f"**{alert.mem_percent:.1f}%** ({alert.mem_usage_mb:.0f}MB / {alert.mem_limit_mb:.0f}MB)",
            inline=True,
        )
        embed.add_field(
            name="🔥 CPU", value=f"**{alert.cpu_percent:.1f}%**", inline=True
        )
        embed.add_field(
            name="⏰ Detected At",
            value=f"<t:{int(alert.timestamp.timestamp())}:T>",
            inline=True,
        )
        embed.add_field(
            name="🎯 Action",
            value="👇 **Click a button below** to restart or skip this container.",
            inline=False,
        )
        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)

        timeout = self.config.restart_timeout if self.config else 30
        authorized = self.config.authorized_role_ids if self.config else []
        view = ContainerActionView(
            container_name=alert.container_name,
            container_id=alert.container_id,
            docker_client=self.docker_client,
            restart_timeout=timeout,
            authorized_role_ids=authorized,
        )
        await channel.send(embed=embed, view=view)

    def send_resource_alert(self, alert):
        """
        Thread-safe method to send a resource spike alert with buttons.
        Can be called from any thread.
        """
        if not self._bot_ready.wait(timeout=15):
            return False
        if self._startup_error:
            return False
        if not self._loop or self._loop.is_closed():
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._send_resource_alert(alert), self._loop
        )
        try:
            future.result(timeout=15)
            return True
        except Exception as e:
            log.error(f"Failed to send resource alert via bot: {e}")
            return False

    async def _send_issue_alert(self, container_info):
        """
        Send a periodic-scan issue alert WITH Restart/Skip buttons via bot.
        This replaces the webhook-only alerter.send_issue_detected() when
        the bot is available.
        """
        if not rate_limiter.allow(container_info.name):
            log.debug(f"Rate-limited bot issue alert for '{container_info.name}' — skipping")
            return

        channel = self.get_channel(self.channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception as e:
                raise RuntimeError(f"Cannot access channel {self.channel_id}: {e}")

        severity = container_info.severity
        color = COLORS.get(severity, COLORS["warning"])

        if severity == "critical":
            title = "🚨 CRITICAL — Container Down!"
            desc = "A container has **crashed** and needs attention."
        else:
            title = "⚠️ WARNING — Container Unhealthy"
            desc = "A container is **not running properly** and may need a restart."

        embed = discord.Embed(
            title=title,
            description=desc,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=DOCKER_THUMBNAIL)
        embed.add_field(
            name="📦 Container",
            value=f"```\n{container_info.name}\n```",
            inline=True,
        )
        embed.add_field(
            name="🏷️ Image",
            value=f"```\n{container_info.image}\n```",
            inline=True,
        )
        embed.add_field(
            name="📊 Status",
            value=f"```\n{container_info.status.upper()}\n```",
            inline=True,
        )
        embed.add_field(
            name="🔍 Diagnosis",
            value=container_info.reason,
            inline=False,
        )
        embed.add_field(
            name="⏱️ Downtime",
            value=f"`{container_info.downtime}`",
            inline=True,
        )
        embed.add_field(
            name="🔢 Exit Code",
            value=f"`{container_info.exit_code}`",
            inline=True,
        )
        embed.add_field(
            name="🆔 Container ID",
            value=f"`{container_info.id_short}`",
            inline=True,
        )

        if container_info.error_msg:
            embed.add_field(
                name="❌ Error Message",
                value=f"```\n{sanitize(container_info.error_msg[:500])}\n```",
                inline=False,
            )

        if container_info.oom_killed:
            embed.add_field(
                name="💀 OOM Killed",
                value=(
                    "Container was killed due to **Out of Memory**.\n"
                    "Consider increasing memory limits."
                ),
                inline=False,
            )

        embed.add_field(
            name="🎯 Action",
            value="👇 **Click a button below** to restart or skip this container.",
            inline=False,
        )
        embed.set_footer(text="docker-socket-watchdog", icon_url=DOCKER_THUMBNAIL)

        timeout = self.config.restart_timeout if self.config else 30
        authorized = self.config.authorized_role_ids if self.config else []
        view = ContainerActionView(
            container_name=container_info.name,
            container_id=container_info.id_short,
            docker_client=self.docker_client,
            restart_timeout=timeout,
            authorized_role_ids=authorized,
        )
        await channel.send(embed=embed, view=view)

    def send_issue_alert(self, container_info):
        """
        Thread-safe method to send a scan issue alert with Restart/Skip buttons.
        Can be called from any thread. Returns True if sent via bot, False to fallback.
        """
        if not self._bot_ready.wait(timeout=15):
            return False
        if self._startup_error:
            return False
        if not self._loop or self._loop.is_closed():
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._send_issue_alert(container_info), self._loop
        )
        try:
            future.result(timeout=15)
            return True
        except Exception as e:
            log.error(f"Failed to send issue alert via bot: {e}")
            return False

    async def _send_plain_embed(self, embed):
        """Send a plain embed (no buttons) via the bot channel."""
        channel = self.get_channel(self.channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception as e:
                raise RuntimeError(f"Cannot access channel {self.channel_id}: {e}")
        await channel.send(embed=embed)

    async def _send_container_dashboard(self):
        """
        Send a startup dashboard showing ALL containers with their names
        and running/stopped status. Includes action buttons for each container.
        """
        channel = self.get_channel(self.channel_id)
        if not channel:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception as e:
                raise RuntimeError(f"Cannot access channel {self.channel_id}: {e}")

        loop = asyncio.get_running_loop()

        def _get_containers():
            containers = self.docker_client.containers.list(all=True)
            result = []
            for c in containers:
                try:
                    state = c.attrs.get("State", {})
                    health = state.get("Health", {}).get("Status", "")
                    image = str(c.image.tags[0]) if c.image.tags else "unknown"
                    result.append({
                        "name": c.name,
                        "status": c.status,
                        "health": health,
                        "image": image,
                        "id": c.short_id,
                    })
                except Exception:
                    result.append({
                        "name": c.name,
                        "status": c.status,
                        "health": "",
                        "image": "unknown",
                        "id": c.short_id,
                    })
            return result

        containers = await loop.run_in_executor(None, _get_containers)

        running = [c for c in containers if c["status"] == "running"]
        stopped = [c for c in containers if c["status"] != "running"]
        total = len(containers)

        # Build the dashboard embed
        embed = discord.Embed(
            title="📊 Container Dashboard",
            description=(
                f"**{total}** total containers — "
                f"**{len(running)}** running, **{len(stopped)}** stopped"
            ),
            color=COLORS["success"] if not stopped else COLORS["warning"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=DOCKER_THUMBNAIL)

        # Running containers section
        if running:
            lines = []
            for c in running:
                health_icon = "💚" if c["health"] == "healthy" else ""
                lines.append(f"🟢 **{c['name']}** {health_icon}\n╰ `{c['image']}` • `{c['id']}`")
            # Discord field value limit is 1024 chars — split if needed
            text = "\n".join(lines)
            if len(text) <= 1024:
                embed.add_field(name=f"✅ Running ({len(running)})", value=text, inline=False)
            else:
                # Split into chunks
                chunk = []
                chunk_len = 0
                part = 1
                for line in lines:
                    if chunk_len + len(line) + 1 > 1000:
                        embed.add_field(
                            name=f"✅ Running (Part {part})",
                            value="\n".join(chunk),
                            inline=False,
                        )
                        chunk = []
                        chunk_len = 0
                        part += 1
                    chunk.append(line)
                    chunk_len += len(line) + 1
                if chunk:
                    embed.add_field(
                        name=f"✅ Running (Part {part})",
                        value="\n".join(chunk),
                        inline=False,
                    )

        # Stopped containers section
        if stopped:
            lines = []
            for c in stopped:
                status_icon = "🔴" if c["status"] == "exited" else "💀" if c["status"] == "dead" else "⚪"
                lines.append(f"{status_icon} **{c['name']}** — `{c['status']}`\n╰ `{c['image']}` • `{c['id']}`")
            text = "\n".join(lines)
            if len(text) <= 1024:
                embed.add_field(name=f"🛑 Stopped ({len(stopped)})", value=text, inline=False)
            else:
                chunk = []
                chunk_len = 0
                part = 1
                for line in lines:
                    if chunk_len + len(line) + 1 > 1000:
                        embed.add_field(
                            name=f"🛑 Stopped (Part {part})",
                            value="\n".join(chunk),
                            inline=False,
                        )
                        chunk = []
                        chunk_len = 0
                        part += 1
                    chunk.append(line)
                    chunk_len += len(line) + 1
                if chunk:
                    embed.add_field(
                        name=f"🛑 Stopped (Part {part})",
                        value="\n".join(chunk),
                        inline=False,
                    )

        embed.set_footer(text="docker-socket-watchdog • Dashboard", icon_url=DOCKER_THUMBNAIL)

        # Create dashboard action buttons
        view = DashboardView(self.docker_client, self.config)
        await channel.send(embed=embed, view=view)

    def send_container_dashboard(self):
        """Thread-safe method to send container dashboard. Called from main thread."""
        if not self._bot_ready.wait(timeout=15):
            return False
        if self._startup_error:
            return False
        if not self._loop or self._loop.is_closed():
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._send_container_dashboard(), self._loop
        )
        try:
            future.result(timeout=30)
            return True
        except Exception as e:
            log.error(f"Failed to send container dashboard via bot: {e}")
            return False

    def send_embed(self, embed_dict):
        """
        Thread-safe method to send any embed via bot (no buttons).
        Used for info events, scan summaries, etc. so ALL messages
        come from the same bot identity.
        """
        if not self._bot_ready.wait(timeout=15):
            return False
        if self._startup_error:
            return False
        if not self._loop or self._loop.is_closed():
            return False

        async def _send():
            channel = self.get_channel(self.channel_id)
            if not channel:
                try:
                    channel = await self.fetch_channel(self.channel_id)
                except Exception as e:
                    raise RuntimeError(f"Cannot access channel {self.channel_id}: {e}")

            embed = discord.Embed.from_dict(embed_dict)
            await channel.send(embed=embed)

        future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
        try:
            future.result(timeout=15)
            return True
        except Exception as e:
            log.error(f"Failed to send embed via bot: {e}")
            return False

    def send_interactive_alert(self, event):
        """
        Thread-safe method to send an interactive alert.
        Can be called from any thread — it schedules the coroutine
        on the bot's async event loop.
        """
        if not self._bot_ready.wait(timeout=15):
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
        Uses the proper 'async with self' pattern to ensure setup_hook,
        view dispatch, and all internal handlers are initialized correctly.
        Returns the thread object.
        """
        def _run():
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)

                async def _runner():
                    async with self:
                        await self.start(self.bot_token)

                self._loop.run_until_complete(_runner())
            except Exception as e:
                log.error(f"Discord bot crashed: {e}")
                self._startup_error = e
                self._bot_ready.set()  # Unblock anyone waiting

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
