"""
Config Module — Loads configuration from config.yaml and .env

Handles:
- YAML config parsing (intervals, container filters, alert settings)
- Environment variable loading via python-dotenv
- Validation and sensible defaults
"""

import os
import yaml
from dotenv import load_dotenv
from sentinel.logger import log


# ─── Default Configuration ─────────────────────────────────────────────────────

DEFAULTS = {
    "sentinel": {
        "check_interval_seconds": 300,
        "max_restart_attempts": 2,
        "restart_timeout": 30,
        "resource_monitoring": {
            "enabled": True,
            "check_interval_seconds": 30,
            "ram_threshold_percent": 90,
            "cpu_threshold_percent": 90,
            "consecutive_breaches": 2,
            "alert_cooldown_seconds": 300,
        },
    },
    "containers": {
        "watch_mode": "all",
        "specific_names": [],
        "exclude_names": [],
    },
    "alerts": {
        "discord": {
            "enabled": True,
            "color_critical": 0xFF3838,
            "color_warning": 0xFFB830,
            "color_success": 0x2ECC71,
            "color_info": 0x3B82F6,
            "color_startup": 0xA855F7,
            "footer_text": "docker-socket-watchdog",
            "footer_icon": "https://cdn-icons-png.flaticon.com/512/5969/5969059.png",
        }
    },
}


# ─── Config Class ──────────────────────────────────────────────────────────────

class Config:
    """Centralized configuration manager."""

    def __init__(self, config_path: str = None):
        # Determine project root (parent of sentinel/)
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Load .env
        env_path = os.path.join(self.project_root, ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
            log.debug(f"Loaded environment from {env_path}")
        else:
            log.warning(f".env file not found at {env_path} — using system env vars")

        # Load config.yaml
        if config_path is None:
            config_path = os.path.join(self.project_root, "config.yaml")

        self._raw = self._load_yaml(config_path)

        # ── Sentinel Settings ──
        sentinel_cfg = self._raw.get("sentinel", {})
        self.check_interval = sentinel_cfg.get(
            "check_interval_seconds",
            DEFAULTS["sentinel"]["check_interval_seconds"],
        )
        self.max_restart_attempts = sentinel_cfg.get(
            "max_restart_attempts",
            DEFAULTS["sentinel"]["max_restart_attempts"],
        )
        self.restart_timeout = sentinel_cfg.get(
            "restart_timeout",
            DEFAULTS["sentinel"]["restart_timeout"],
        )

        # ── Resource Monitoring Settings ──
        res_defaults = DEFAULTS["sentinel"]["resource_monitoring"]
        res_cfg = sentinel_cfg.get("resource_monitoring", {})
        self.resource_monitoring_enabled = res_cfg.get(
            "enabled", res_defaults["enabled"]
        )
        self.resource_check_interval = res_cfg.get(
            "check_interval_seconds", res_defaults["check_interval_seconds"]
        )
        self.ram_threshold_percent = res_cfg.get(
            "ram_threshold_percent", res_defaults["ram_threshold_percent"]
        )
        self.cpu_threshold_percent = res_cfg.get(
            "cpu_threshold_percent", res_defaults["cpu_threshold_percent"]
        )
        self.resource_consecutive_breaches = res_cfg.get(
            "consecutive_breaches", res_defaults["consecutive_breaches"]
        )
        self.resource_alert_cooldown = res_cfg.get(
            "alert_cooldown_seconds", res_defaults["alert_cooldown_seconds"]
        )

        # ── Container Settings ──
        containers_cfg = self._raw.get("containers", {})
        self.watch_mode = containers_cfg.get(
            "watch_mode",
            DEFAULTS["containers"]["watch_mode"],
        )
        self.specific_names = containers_cfg.get(
            "specific_names",
            DEFAULTS["containers"]["specific_names"],
        ) or []
        self.exclude_names = containers_cfg.get(
            "exclude_names",
            DEFAULTS["containers"]["exclude_names"],
        ) or []

        # ── Discord Settings ──
        discord_cfg = self._raw.get("alerts", {}).get("discord", {})
        discord_defaults = DEFAULTS["alerts"]["discord"]

        self.discord_enabled = discord_cfg.get("enabled", discord_defaults["enabled"])
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

        # Bot settings (for interactive buttons)
        self.discord_bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
        _channel_id = os.getenv("DISCORD_CHANNEL_ID", "")
        self.discord_channel_id = int(_channel_id) if _channel_id.isdigit() else 0

        self.discord_colors = {
            "critical": discord_cfg.get("color_critical", discord_defaults["color_critical"]),
            "warning": discord_cfg.get("color_warning", discord_defaults["color_warning"]),
            "success": discord_cfg.get("color_success", discord_defaults["color_success"]),
            "info": discord_cfg.get("color_info", discord_defaults["color_info"]),
            "startup": discord_cfg.get("color_startup", discord_defaults["color_startup"]),
        }

        self.discord_footer_text = discord_cfg.get(
            "footer_text", discord_defaults["footer_text"]
        )
        self.discord_footer_icon = discord_cfg.get(
            "footer_icon", discord_defaults["footer_icon"]
        )

        self._validate()

    def _load_yaml(self, path: str) -> dict:
        """Load and parse config.yaml."""
        if not os.path.exists(path):
            log.warning(f"Config file not found at {path} — using defaults")
            return {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            log.info(f"Configuration loaded from {os.path.basename(path)}")
            return data
        except yaml.YAMLError as e:
            log.error(f"Failed to parse config.yaml: {e}")
            return {}

    @property
    def discord_bot_enabled(self) -> bool:
        """Check if Discord bot (interactive buttons) is configured."""
        return bool(self.discord_bot_token and self.discord_channel_id)

    def _validate(self):
        """Validate critical configuration values."""
        if self.discord_enabled and not self.discord_webhook_url:
            log.warning(
                "Discord alerts enabled but DISCORD_WEBHOOK_URL not set in .env — "
                "alerts will be skipped"
            )

        if self.discord_bot_token and not self.discord_channel_id:
            log.warning(
                "DISCORD_BOT_TOKEN set but DISCORD_CHANNEL_ID missing — "
                "interactive buttons will be disabled"
            )

        if self.watch_mode == "specific" and not self.specific_names:
            log.warning(
                "Watch mode is 'specific' but no container names provided — "
                "nothing will be monitored"
            )

        if self.check_interval < 10:
            log.warning(
                f"Check interval ({self.check_interval}s) is very low — "
                "setting minimum to 10s"
            )
            self.check_interval = 10

    def summary(self) -> str:
        """Return a human-readable config summary."""
        interval_min = self.check_interval / 60
        lines = [
            f"  Watch Mode       : {self.watch_mode}",
            f"  Check Interval   : {self.check_interval}s ({interval_min:.1f} min)",
            f"  Max Restarts     : {self.max_restart_attempts}",
            f"  Restart Timeout  : {self.restart_timeout}s",
            f"  Discord Alerts   : {'✅ Enabled' if self.discord_enabled else '❌ Disabled'}",
            f"  Discord Bot      : {'✅ Interactive Buttons' if self.discord_bot_enabled else '➖ Not configured'}",
            f"  Resource Monitor : {'✅ Enabled' if self.resource_monitoring_enabled else '❌ Disabled'}",
        ]

        if self.resource_monitoring_enabled:
            lines.append(
                f"  RAM / CPU Thresh : {self.ram_threshold_percent}% / {self.cpu_threshold_percent}%"
            )

        if self.watch_mode == "specific":
            lines.append(f"  Watching         : {', '.join(self.specific_names)}")

        if self.exclude_names:
            lines.append(f"  Excluding        : {', '.join(self.exclude_names)}")

        return "\n".join(lines)
