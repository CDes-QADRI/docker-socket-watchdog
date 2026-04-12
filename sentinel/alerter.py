"""
Alerter Module — Beautiful Discord webhook notifications.

Sends rich, visually stunning embed messages to Discord with:
- Color-coded severity levels
- Detailed container diagnostics
- Branded footer with timestamps
- Action confirmation status
"""

import requests
from datetime import datetime, timezone
from sentinel.logger import log
from sentinel.config import Config
from sentinel.monitor import ContainerInfo


class DiscordAlerter:
    """Sends beautiful Discord embed notifications via webhooks."""

    # Docker logo for thumbnails
    DOCKER_THUMBNAIL = "https://cdn-icons-png.flaticon.com/512/5969/5969059.png"
    SHIELD_ICON = "https://cdn-icons-png.flaticon.com/512/6941/6941697.png"

    def __init__(self, config: Config):
        self.config = config
        self.webhook_url = config.discord_webhook_url
        self.colors = config.discord_colors
        self.footer_text = config.discord_footer_text
        self.footer_icon = config.discord_footer_icon

    def _send(self, payload: dict) -> bool:
        """Send a payload to the Discord webhook."""
        if not self.webhook_url:
            log.warning("Discord webhook URL not configured — skipping alert")
            return False

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            if response.status_code in (200, 204):
                log.debug("Discord alert sent successfully")
                return True
            else:
                log.error(
                    f"Discord webhook returned {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return False
        except requests.RequestException as e:
            log.error(f"Failed to send Discord alert: {e}")
            return False

    # ─── Real-Time Event Alert ─────────────────────────────────────────────────

    def send_realtime_event(self, event):
        """Send an instant notification for a real-time Docker event."""
        severity = event.severity
        color = self.colors.get(severity, self.colors['info'])

        fields = [
            {
                "name": "📦 Container",
                "value": f"`{event.container_name}`",
                "inline": True,
            },
            {
                "name": "🏷️ Image",
                "value": f"`{event.image}`",
                "inline": True,
            },
            {
                "name": "🔢 Exit Code",
                "value": f"`{event.exit_code}`" if event.exit_code else "`N/A`",
                "inline": True,
            },
            {
                "name": "🆔 Container ID",
                "value": f"`{event.container_id}`",
                "inline": True,
            },
            {
                "name": "⏰ Detected At",
                "value": f"<t:{int(event.timestamp.timestamp())}:T>",
                "inline": True,
            },
        ]

        if event.needs_attention:
            fields.append({
                "name": "🎯 Action Required",
                "value": (
                    "⚡ **Check your Sentinel terminal** — "
                    "confirmation prompt awaiting your response."
                ),
                "inline": False,
            })

        embed = {
            "title": f"{event.emoji} {event.description}",
            "description": f"**{event.container_name}** → `{event.action}`",
            "color": color,
            "thumbnail": {"url": self.DOCKER_THUMBNAIL},
            "fields": fields,
            "footer": {
                "text": self.footer_text,
                "icon_url": self.footer_icon,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "docker-socket-watchdog",
            "avatar_url": self.SHIELD_ICON,
            "embeds": [embed],
        }

        return self._send(payload)

    # ─── Startup Alert ─────────────────────────────────────────────────────────

    def send_startup(self, docker_info: dict, config_summary: str):
        """Send a 'Sentinel started' notification."""
        total = docker_info.get("containers_total", "?")
        running = docker_info.get("containers_running", "?")
        stopped = docker_info.get("containers_stopped", "?")
        version = docker_info.get("docker_version", "?")

        embed = {
            "title": "🛡️ docker-socket-watchdog Activated",
            "description": (
                "**The sentinel is now watching your containers.**\n"
                "Unhealthy or crashed containers will be detected and you'll be notified instantly."
            ),
            "color": self.colors["startup"],
            "thumbnail": {"url": self.SHIELD_ICON},
            "fields": [
                {
                    "name": "🐳 Docker Environment",
                    "value": (
                        f"```\n"
                        f"Docker Version : {version}\n"
                        f"Total          : {total}\n"
                        f"Running        : {running}\n"
                        f"Stopped        : {stopped}\n"
                        f"```"
                    ),
                    "inline": False,
                },
                {
                    "name": "⚙️ Configuration",
                    "value": f"```\n{config_summary}\n```",
                    "inline": False,
                },
            ],
            "footer": {
                "text": self.footer_text,
                "icon_url": self.footer_icon,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "docker-socket-watchdog",
            "avatar_url": self.SHIELD_ICON,
            "embeds": [embed],
        }

        return self._send(payload)

    # ─── Container Issue Alert ─────────────────────────────────────────────────

    def send_issue_detected(self, container_info: ContainerInfo):
        """Send a beautiful alert when a container issue is detected."""

        severity = container_info.severity
        color = self.colors.get(severity, self.colors["warning"])

        # Severity-specific styling
        if severity == "critical":
            title = "🚨 CRITICAL — Container Down!"
            desc_prefix = "A container has **crashed** and needs attention."
        else:
            title = "⚠️ WARNING — Container Unhealthy"
            desc_prefix = "A container is **not running properly** and may need a restart."

        # Build detailed fields
        fields = [
            {
                "name": "📦 Container",
                "value": f"```\n{container_info.name}\n```",
                "inline": True,
            },
            {
                "name": "🏷️ Image",
                "value": f"```\n{container_info.image}\n```",
                "inline": True,
            },
            {
                "name": "📊 Status",
                "value": f"```\n{container_info.status.upper()}\n```",
                "inline": True,
            },
            {
                "name": "🔍 Diagnosis",
                "value": container_info.reason,
                "inline": False,
            },
            {
                "name": "⏱️ Downtime",
                "value": f"`{container_info.downtime}`",
                "inline": True,
            },
            {
                "name": "🔢 Exit Code",
                "value": f"`{container_info.exit_code}`",
                "inline": True,
            },
            {
                "name": "🆔 Container ID",
                "value": f"`{container_info.id_short}`",
                "inline": True,
            },
        ]

        # Add error message if present
        if container_info.error_msg:
            fields.append({
                "name": "❌ Error Message",
                "value": f"```\n{container_info.error_msg[:500]}\n```",
                "inline": False,
            })

        # Add OOM warning
        if container_info.oom_killed:
            fields.append({
                "name": "💀 OOM Killed",
                "value": (
                    "Container was killed due to **Out of Memory**.\n"
                    "Consider increasing memory limits."
                ),
                "inline": False,
            })

        fields.append({
            "name": "🎯 Action Required",
            "value": (
                "**Awaiting your confirmation in the terminal** to restart this container.\n"
                "Respond in the Sentinel terminal to proceed."
            ),
            "inline": False,
        })

        embed = {
            "title": title,
            "description": desc_prefix,
            "color": color,
            "thumbnail": {"url": self.DOCKER_THUMBNAIL},
            "fields": fields,
            "footer": {
                "text": self.footer_text,
                "icon_url": self.footer_icon,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "docker-socket-watchdog",
            "avatar_url": self.SHIELD_ICON,
            "embeds": [embed],
        }

        return self._send(payload)

    # ─── Restart Result Alert ──────────────────────────────────────────────────

    def send_restart_result(self, container_info: ContainerInfo, success: bool, skipped: bool = False):
        """Send a notification about the restart outcome."""

        if skipped:
            title = "⏭️ Restart Skipped"
            description = f"User chose to **skip** restarting `{container_info.name}`."
            color = self.colors["info"]
        elif success:
            title = "✅ Container Restarted Successfully!"
            description = (
                f"Container `{container_info.name}` has been **restarted** and is now running."
            )
            color = self.colors["success"]
        else:
            title = "❌ Restart Failed!"
            description = (
                f"Failed to restart `{container_info.name}`.\n"
                f"Manual intervention may be required."
            )
            color = self.colors["critical"]

        fields = [
            {
                "name": "📦 Container",
                "value": f"`{container_info.name}`",
                "inline": True,
            },
            {
                "name": "🏷️ Image",
                "value": f"`{container_info.image}`",
                "inline": True,
            },
        ]

        if not skipped:
            fields.append({
                "name": "🔄 Result",
                "value": "🟢 Running" if success else "🔴 Still Down",
                "inline": True,
            })

        embed = {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {
                "text": self.footer_text,
                "icon_url": self.footer_icon,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "docker-socket-watchdog",
            "avatar_url": self.SHIELD_ICON,
            "embeds": [embed],
        }

        return self._send(payload)

    # ─── All Clear Alert ───────────────────────────────────────────────────────

    def send_all_clear(self, total_containers: int):
        """Send 'everything is healthy' notification."""

        embed = {
            "title": "💚 All Systems Healthy",
            "description": (
                f"All **{total_containers}** monitored containers are running normally.\n"
                f"No action required."
            ),
            "color": self.colors["success"],
            "thumbnail": {"url": self.SHIELD_ICON},
            "footer": {
                "text": self.footer_text,
                "icon_url": self.footer_icon,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "docker-socket-watchdog",
            "avatar_url": self.SHIELD_ICON,
            "embeds": [embed],
        }

        return self._send(payload)

    # ─── Scan Summary Alert ────────────────────────────────────────────────────

    def send_scan_summary(self, total: int, healthy: int, problematic: int, actions_taken: list):
        """Send a summary embed after a full scan cycle."""

        status_bar = ""
        if total > 0:
            healthy_pct = (healthy / total) * 100
            bar_len = 20
            filled = int((healthy / total) * bar_len)
            status_bar = f"`[{'█' * filled}{'░' * (bar_len - filled)}]` {healthy_pct:.0f}% healthy"

        fields = [
            {
                "name": "📊 Scan Results",
                "value": (
                    f"```\n"
                    f"Total Monitored  : {total}\n"
                    f"Healthy          : {healthy}\n"
                    f"Problematic      : {problematic}\n"
                    f"```"
                ),
                "inline": False,
            },
            {
                "name": "📈 Health Bar",
                "value": status_bar or "`No containers found`",
                "inline": False,
            },
        ]

        if actions_taken:
            actions_text = "\n".join(
                f"• {action}" for action in actions_taken[-10:]  # Last 10 actions
            )
            fields.append({
                "name": "🔄 Actions Taken",
                "value": actions_text,
                "inline": False,
            })

        embed = {
            "title": "📋 Scan Cycle Complete",
            "color": (
                self.colors["success"] if problematic == 0
                else self.colors["warning"]
            ),
            "fields": fields,
            "footer": {
                "text": self.footer_text,
                "icon_url": self.footer_icon,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "docker-socket-watchdog",
            "avatar_url": self.SHIELD_ICON,
            "embeds": [embed],
        }

        return self._send(payload)

    # ─── Shutdown Alert ────────────────────────────────────────────────────────

    def send_shutdown(self):
        """Send a graceful shutdown notification."""

        embed = {
            "title": "🔴 docker-socket-watchdog Offline",
            "description": "The sentinel has been **shut down** gracefully.\nContainers are no longer being monitored.",
            "color": self.colors["critical"],
            "footer": {
                "text": self.footer_text,
                "icon_url": self.footer_icon,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "docker-socket-watchdog",
            "avatar_url": self.SHIELD_ICON,
            "embeds": [embed],
        }

        return self._send(payload)
