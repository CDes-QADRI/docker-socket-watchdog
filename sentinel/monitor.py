"""
Monitor Module — Docker container health checker.

Connects to the Docker daemon and inspects container states.
Detects: exited, dead, unhealthy, and OOM-killed containers.
"""

import docker
import time
import threading
from docker.errors import DockerException
from datetime import datetime, timezone
from sentinel.logger import log
from sentinel.config import Config


# ─── Container Info ────────────────────────────────────────────────────────────

class ContainerInfo:
    """Holds diagnostic information about a problematic container."""

    def __init__(self, container):
        self.container = container
        self.name = container.name
        self.id_short = container.short_id
        self.image = str(container.image.tags[0]) if container.image.tags else "unknown"
        self.status = container.status  # "exited", "dead", "running", etc.

        # Detailed state info
        state = container.attrs.get("State", {})
        self.exit_code = state.get("ExitCode", -1)
        self.oom_killed = state.get("OOMKilled", False)
        self.error_msg = state.get("Error", "")
        self.finished_at = state.get("FinishedAt", "")

        # Health check info (if configured)
        health = state.get("Health", {})
        self.health_status = health.get("Status", "none")

        # Calculate downtime
        self.downtime = self._calc_downtime()

    def _calc_downtime(self) -> str:
        """Calculate how long the container has been down."""
        if not self.finished_at or self.finished_at.startswith("0001"):
            return "unknown"

        try:
            # Parse ISO timestamp (Docker uses RFC3339)
            finished = datetime.fromisoformat(
                self.finished_at.replace("Z", "+00:00")
            )
            delta = datetime.now(timezone.utc) - finished
            total_seconds = int(delta.total_seconds())

            if total_seconds < 60:
                return f"{total_seconds}s ago"
            elif total_seconds < 3600:
                return f"{total_seconds // 60}m {total_seconds % 60}s ago"
            else:
                hours = total_seconds // 3600
                mins = (total_seconds % 3600) // 60
                return f"{hours}h {mins}m ago"
        except (ValueError, TypeError):
            return "unknown"

    @property
    def severity(self) -> str:
        """Determine severity level."""
        if self.oom_killed:
            return "critical"
        if self.exit_code != 0 and self.status == "exited":
            return "critical"
        if self.health_status == "unhealthy":
            return "warning"
        if self.status in ("exited", "dead"):
            return "warning"
        return "info"

    @property
    def reason(self) -> str:
        """Human-readable reason for the issue."""
        if self.oom_killed:
            return "💀 OOM Killed (out of memory)"
        if self.status == "dead":
            return "☠️ Container is dead"
        if self.status == "exited" and self.exit_code != 0:
            return f"💥 Crashed with exit code {self.exit_code}"
        if self.status == "exited" and self.exit_code == 0:
            return "⏹️ Stopped (exit code 0)"
        if self.health_status == "unhealthy":
            return "🤒 Health check failing"
        return f"Unknown issue (status: {self.status})"

    def __repr__(self):
        return f"<ContainerInfo name={self.name} status={self.status} exit={self.exit_code}>"


# ─── Monitor Class ─────────────────────────────────────────────────────────────

class DockerMonitor:
    """Monitors Docker containers for health issues."""

    def __init__(self, config: Config):
        self.config = config
        self.client = None

    def connect(self) -> bool:
        """Connect to the Docker daemon with retry."""
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                self.client = docker.from_env()
                self.client.ping()
                log.info("Connected to Docker daemon")
                return True
            except DockerException as e:
                log.error(f"Cannot connect to Docker daemon (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(2)
                else:
                    log.error(
                        "Make sure Docker is running and you have permission "
                        "(try: sudo usermod -aG docker $USER)"
                    )
        return False

    def get_docker_info(self) -> dict:
        """Get basic Docker system info."""
        if not self.client:
            return {}
        try:
            info = self.client.info()
            return {
                "containers_total": info.get("Containers", 0),
                "containers_running": info.get("ContainersRunning", 0),
                "containers_stopped": info.get("ContainersStopped", 0),
                "containers_paused": info.get("ContainersPaused", 0),
                "docker_version": info.get("ServerVersion", "unknown"),
            }
        except DockerException:
            return {}

    def scan(self):
        """
        Scan all containers and return problematic ones.

        Returns:
            tuple: (problematic_containers, all_containers)
        """
        if not self.client:
            if not self.connect():
                return [], []

        try:
            # Get all containers (including stopped)
            all_containers = self.client.containers.list(all=True)
        except DockerException as e:
            log.error(f"Failed to list containers: {e}")
            return [], []

        # Filter based on watch_mode
        watched = self._filter_containers(all_containers)

        # Find problematic containers
        problematic = []
        for container in watched:
            # Reload to get fresh state
            try:
                container.reload()
            except DockerException:
                continue

            status = container.status
            health = container.attrs.get("State", {}).get("Health", {}).get("Status", "none")

            is_problematic = (
                status in ("exited", "dead")
                or health == "unhealthy"
            )

            if is_problematic:
                info = ContainerInfo(container)
                problematic.append(info)

        return problematic, watched

    def _filter_containers(self, containers) -> list:
        """Apply watch_mode and exclusion filters."""
        filtered = []

        for container in containers:
            name = container.name

            # Skip excluded containers
            if name in self.config.exclude_names:
                continue

            if self.config.watch_mode == "specific":
                # Only include specifically named containers
                if name in self.config.specific_names:
                    filtered.append(container)
            else:
                # "all" mode — include everything not excluded
                filtered.append(container)

        return filtered


# ─── Container Event (Real-time) ───────────────────────────────────────────────

class ContainerEvent:
    """Represents a real-time Docker container event."""

    # Exit codes from signals (intentional stop, not crash)
    SIGNAL_EXIT_CODES = {'0', '137', '143'}

    def __init__(self, event_data: dict, client):
        actor = event_data.get('Actor', {})
        attrs = actor.get('Attributes', {})

        self.raw_action = event_data.get('Action', '')
        self.action = self.raw_action.split(':')[0].strip()
        self.sub_action = (
            self.raw_action.split(':')[1].strip()
            if ':' in self.raw_action else ''
        )

        self.container_id = actor.get('ID', '')[:12]
        self.container_name = attrs.get('name', 'unknown')
        self.image = attrs.get('image', 'unknown')
        self.exit_code = attrs.get('exitCode', '')
        self.timestamp = datetime.fromtimestamp(
            event_data.get('time', 0), tz=timezone.utc
        )

        # Try to get the actual container object
        self.container = None
        try:
            self.container = client.containers.get(self.container_id)
        except Exception:
            pass

    @property
    def severity(self) -> str:
        """Determine event severity."""
        if self.action == 'oom':
            return 'critical'
        if self.action == 'die':
            if self.exit_code and self.exit_code not in self.SIGNAL_EXIT_CODES:
                return 'critical'
            return 'warning'
        if self.action == 'destroy':
            return 'warning'
        if self.sub_action == 'unhealthy':
            return 'warning'
        if self.action in ('start', 'restart'):
            return 'success'
        if self.sub_action == 'healthy':
            return 'success'
        return 'info'

    @property
    def emoji(self) -> str:
        """Get visual emoji for event type."""
        mapping = {
            'create': '📦',
            'start': '🟢',
            'die': '💀',
            'oom': '💥',
            'restart': '🔄',
            'destroy': '🗑️',
        }
        if self.action == 'health_status':
            return '🏥' if self.sub_action == 'unhealthy' else '💚'
        return mapping.get(self.action, '📋')

    @property
    def description(self) -> str:
        """Human-readable description of the event."""
        if self.action == 'die':
            if self.exit_code == '0':
                return 'Container stopped (exit code 0)'
            elif self.exit_code in ('137', '143'):
                return f'Container stopped by signal (exit code {self.exit_code})'
            else:
                return f'Container CRASHED (exit code {self.exit_code})'
        if self.action == 'oom':
            return 'Container killed — Out of Memory!'
        if self.action == 'health_status':
            if self.sub_action == 'unhealthy':
                return 'Health check FAILING'
            return 'Health check recovered'
        descriptions = {
            'create': 'New container created',
            'start': 'Container started',
            'restart': 'Container restarted',
            'destroy': 'Container removed',
        }
        return descriptions.get(self.action, f'Container event: {self.action}')

    @property
    def needs_attention(self) -> bool:
        """Does this event need user attention (restart prompt)?"""
        if self.action == 'oom':
            return True
        if self.action == 'die' and self.exit_code not in self.SIGNAL_EXIT_CODES:
            return True
        if self.sub_action == 'unhealthy':
            return True
        return False

    def to_container_info(self):
        """Convert to ContainerInfo if the container still exists."""
        if not self.container:
            return None
        try:
            self.container.reload()
            return ContainerInfo(self.container)
        except Exception:
            return None

    def __repr__(self):
        return (
            f"<ContainerEvent {self.emoji} {self.container_name} "
            f"action={self.action} exit={self.exit_code}>"
        )


# ─── Docker Event Listener ────────────────────────────────────────────────────

class DockerEventListener:
    """
    Listens to the Docker daemon event stream in real-time.
    Fires a callback instantly whenever a container event occurs.
    """

    # Actions we care about (skip 'kill' and 'stop' to avoid duplicates with 'die')
    WATCHED_ACTIONS = {
        'create', 'start', 'die', 'oom', 'restart', 'destroy', 'health_status'
    }

    def __init__(self, config: Config, client):
        self.config = config
        self.client = client
        self._stop_event = threading.Event()

    def listen(self, callback):
        """
        Block and listen for Docker events. Calls callback(ContainerEvent)
        for each relevant event. Run this in a daemon thread.
        Auto-reconnects if the connection drops.
        """
        while not self._stop_event.is_set():
            try:
                log.info("🔌 Real-time event listener connected")
                events = self.client.events(
                    decode=True, filters={'type': 'container'}
                )
                for event in events:
                    if self._stop_event.is_set():
                        return

                    action = event.get('Action', '').split(':')[0].strip()
                    if action not in self.WATCHED_ACTIONS:
                        continue

                    # Get container name for filtering
                    container_name = (
                        event.get('Actor', {})
                        .get('Attributes', {})
                        .get('name', '')
                    )
                    if not self._should_watch(container_name):
                        continue

                    try:
                        container_event = ContainerEvent(event, self.client)
                        callback(container_event)
                    except Exception as e:
                        log.error(f"Error processing event: {e}")

            except Exception as e:
                if not self._stop_event.is_set():
                    log.warning(
                        f"Event listener disconnected: {e}. "
                        f"Reconnecting in 5s..."
                    )
                    time.sleep(5)

    def _should_watch(self, name: str) -> bool:
        """Check if this container should be monitored."""
        if not name:
            return True
        if name in self.config.exclude_names:
            return False
        if self.config.watch_mode == 'specific':
            return name in self.config.specific_names
        return True

    def stop(self):
        """Signal the listener to stop."""
        self._stop_event.set()
