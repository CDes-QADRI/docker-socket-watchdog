#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║              docker-socket-watchdog — Main Entry Point       ║
║              Automated Docker Service Healer                 ║
╚══════════════════════════════════════════════════════════════╝

Usage:
    python main.py                  # Start monitoring (real-time + periodic)
    python main.py --once           # Run a single scan and exit
    python main.py --interval 60    # Override periodic scan interval (seconds)
    python main.py --watch-only     # Only notify, never prompt for restarts

Architecture:
- Thread 1 (Background): Listens to Docker event stream in real-time.
  Any container create/start/stop/crash/remove is detected INSTANTLY
  and a Discord notification is sent within seconds.
- Thread 2 (Main): Handles user confirmations for restart prompts,
  and runs periodic full scans as a safety net.
"""

import sys
import time
import signal
import argparse
import queue
import threading
from datetime import datetime
from colorama import Fore, Style, init as colorama_init

from sentinel.config import Config
from sentinel.logger import log, print_banner, print_separator, print_container_status
from sentinel.monitor import DockerMonitor, DockerEventListener, ContainerInfo
from sentinel.healer import ContainerHealer
from sentinel.alerter import DiscordAlerter
from sentinel.discord_bot import SentinelBot, is_bot_available


# ─── Globals ───────────────────────────────────────────────────────────────────

shutdown_requested = False
sentinel_bot = None  # Global reference to Discord bot (if enabled)


def signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global shutdown_requested
    shutdown_requested = True
    print(f"\n\n  {Fore.YELLOW}{Style.BRIGHT}⚡ Shutdown signal received. Cleaning up...{Style.RESET_ALL}\n")


# ─── Handle Problematic Event ─────────────────────────────────────────────────

def handle_problematic_event(event, healer, alerter):
    """
    Handle a single problematic event: show details and ask user for
    restart confirmation in the terminal.
    """
    container_info = event.to_container_info()
    if not container_info:
        log.warning(
            f"Cannot access container '{event.container_name}' — "
            f"it may have been removed already"
        )
        return None

    choice = healer.request_confirmation(container_info)

    if choice in ('y', 'a'):
        success = healer.restart(container_info)
        alerter.send_restart_result(container_info, success=success)
    else:
        log.info(f"Skipping restart of '{event.container_name}'")
        alerter.send_restart_result(container_info, success=False, skipped=True)

    return choice


# ─── Periodic Scan Cycle ───────────────────────────────────────────────────────

def run_scan_cycle(monitor, healer, alerter, watch_only=False):
    """
    Execute one complete periodic scan cycle:
    1. Scan containers
    2. Display status
    3. Alert on issues
    4. Request confirmation & heal (unless watch_only)
    """
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print_separator(f"PERIODIC SCAN @ {scan_time}")

    # ── Scan ──
    problematic, all_watched = monitor.scan()
    total = len(all_watched)
    healthy_count = total - len(problematic)

    if total == 0:
        log.warning("No containers found to monitor")
        return

    # ── Display all container statuses ──
    log.info(f"Scanned {total} container(s)")
    print()

    for container in all_watched:
        try:
            container.reload()
        except Exception:
            pass

        status = container.status
        health = container.attrs.get("State", {}).get("Health", {}).get("Status", "")

        if status == "running" and health != "unhealthy":
            color = Fore.GREEN
            display_status = "● RUNNING"
        elif health == "unhealthy":
            color = Fore.YELLOW
            display_status = "▲ UNHEALTHY"
        elif status == "exited":
            color = Fore.RED
            display_status = "✖ EXITED"
        elif status == "dead":
            color = Fore.RED
            display_status = "☠ DEAD"
        else:
            color = Fore.CYAN
            display_status = f"? {status.upper()}"

        print_container_status(container.name, display_status, color)

    print()

    # ── Handle Problematic Containers ──
    actions_taken = []

    if not problematic:
        log.info(f"All {total} containers are healthy ✨")
    else:
        log.warning(f"Found {len(problematic)} problematic container(s)")

        if watch_only:
            for info in problematic:
                alerter.send_issue_detected(info)
                actions_taken.append(f"🔔 `{info.name}` — notified (watch-only mode)")
        else:
            # Send Discord alerts for all problematic containers
            for info in problematic:
                alerter.send_issue_detected(info)

            # Use numbered batch confirmation
            decisions = healer.request_batch_confirmation(problematic)

            for info in problematic:
                action = decisions.get(info.name, 'skip')
                if action == 'restart':
                    success = healer.restart(info)
                    alerter.send_restart_result(info, success=success)
                    actions_taken.append(
                        f"{'✅' if success else '❌'} `{info.name}` — "
                        f"{'restarted' if success else 'restart failed'}"
                    )
                else:
                    log.info(f"Skipping '{info.name}' per user request")
                    alerter.send_restart_result(info, success=False, skipped=True)
                    actions_taken.append(f"⏭️ `{info.name}` — skipped (user)")

    # ── Send scan summary to Discord ──
    alerter.send_scan_summary(
        total=total,
        healthy=healthy_count,
        problematic=len(problematic),
        actions_taken=actions_taken,
    )

    print_separator()


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    global shutdown_requested

    colorama_init(autoreset=True)

    # ── Parse arguments ──
    parser = argparse.ArgumentParser(
        description="docker-socket-watchdog — Automated Docker Service Healer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan and exit",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Override periodic scan interval in seconds",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument(
        "--watch-only",
        action="store_true",
        help="Only monitor and notify — never prompt for restarts",
    )
    args = parser.parse_args()

    # ── Print banner ──
    print_banner()

    # ── Load config ──
    config = Config(config_path=args.config)

    if args.interval:
        config.check_interval = max(args.interval, 10)

    print_separator("CONFIGURATION")
    print(config.summary())
    if args.watch_only:
        print(f"  Mode             : 👁️ Watch-Only (no restart prompts)")
    print()

    # ── Initialize components ──
    monitor = DockerMonitor(config)
    healer = ContainerHealer(config)
    alerter = DiscordAlerter(config)

    # ── Connect to Docker ──
    if not monitor.connect():
        log.error("Cannot proceed without Docker connection. Exiting.")
        sys.exit(1)

    # ── Docker info ──
    docker_info = monitor.get_docker_info()
    if docker_info:
        print_separator("DOCKER INFO")
        print(f"  Docker Version  : {docker_info.get('docker_version', '?')}")
        print(f"  Total Containers: {docker_info.get('containers_total', '?')}")
        print(f"  Running         : {docker_info.get('containers_running', '?')}")
        print(f"  Stopped         : {docker_info.get('containers_stopped', '?')}")
        print()

    # ── Send startup alert ──
    alerter.send_startup(docker_info, config.summary())

    # ── Start Discord Bot (if configured) ──
    global sentinel_bot
    if config.discord_bot_enabled and is_bot_available():
        log.info("🤖 Starting Discord Bot for interactive buttons...")
        sentinel_bot = SentinelBot(
            bot_token=config.discord_bot_token,
            channel_id=config.discord_channel_id,
            docker_client=monitor.client,
            config=config,
        )
        sentinel_bot.run_in_thread()
        # Give the bot a moment to connect
        import time as _t
        _t.sleep(3)
    elif config.discord_bot_token and not is_bot_available():
        log.warning(
            "DISCORD_BOT_TOKEN is set but discord.py is not installed — "
            "run: pip install discord.py"
        )
    else:
        log.info("ℹ️ Discord Bot not configured — using webhook-only mode (no interactive buttons)")

    # ── Register signal handlers ──
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # ── Run ──
    if args.once:
        log.info("Running single scan (--once mode)")
        run_scan_cycle(monitor, healer, alerter, watch_only=args.watch_only)
        log.info("Single scan complete. Exiting.")
    else:
        # ═══════════════════════════════════════════════════════════════════
        # EVENT-DRIVEN ARCHITECTURE
        # ═══════════════════════════════════════════════════════════════════

        # Queue: events thread puts crash events here → main thread processes
        event_q = queue.Queue()

        def on_docker_event(container_event):
            """
            Callback from the events thread (runs in background thread).
            Sends Discord notification IMMEDIATELY, then queues crash events
            for the main thread to handle confirmation.
            """
            # Always send instant Discord notification
            # If bot is available, use it for events needing attention (buttons)
            # Otherwise fall back to webhook
            if sentinel_bot and container_event.needs_attention:
                sent = sentinel_bot.send_interactive_alert(container_event)
                if not sent:
                    # Fallback to webhook if bot fails
                    alerter.send_realtime_event(container_event)
            else:
                alerter.send_realtime_event(container_event)

            # Log to terminal
            log.info(
                f"{container_event.emoji} "
                f"{container_event.container_name} → "
                f"{container_event.description}"
            )

            # If it needs attention and not watch-only, queue for confirmation
            if container_event.needs_attention and not args.watch_only:
                event_q.put(container_event)

        # Start the real-time event listener in a daemon thread
        listener = DockerEventListener(config, monitor.client)
        events_thread = threading.Thread(
            target=listener.listen,
            args=(on_docker_event,),
            daemon=True,
        )
        events_thread.start()

        interval = config.check_interval
        log.info(
            f"⚡ Real-time event monitoring ACTIVE — "
            f"instant Discord alerts on any container change"
        )
        log.info(
            f"📊 Periodic full scan every {interval}s ({interval/60:.1f} min)"
        )
        log.info("Press Ctrl+C to stop\n")

        # Initial scan
        run_scan_cycle(monitor, healer, alerter, watch_only=args.watch_only)

        # ── Main Loop ──
        last_scan_time = time.time()

        while not shutdown_requested:
            # Process any queued crash events (from events thread)
            try:
                event = event_q.get_nowait()
                result = handle_problematic_event(event, healer, alerter)
                # If user chose 'skip all', drain remaining events
                if result == 's':
                    while not event_q.empty():
                        try:
                            skip_event = event_q.get_nowait()
                            ci = skip_event.to_container_info()
                            if ci:
                                alerter.send_restart_result(ci, False, skipped=True)
                        except queue.Empty:
                            break
                # If user chose 'restart all', restart remaining events
                elif result == 'a':
                    while not event_q.empty():
                        try:
                            auto_event = event_q.get_nowait()
                            ci = auto_event.to_container_info()
                            if ci:
                                success = healer.restart(ci)
                                alerter.send_restart_result(ci, success=success)
                        except queue.Empty:
                            break
            except queue.Empty:
                pass

            # Check if it's time for a periodic scan
            if time.time() - last_scan_time >= interval:
                run_scan_cycle(monitor, healer, alerter, watch_only=args.watch_only)
                last_scan_time = time.time()

            # Sleep briefly before next iteration
            time.sleep(0.5)

        # ── Graceful Shutdown ──
        listener.stop()

    log.info("Sending shutdown notification...")
    alerter.send_shutdown()
    log.info("docker-socket-watchdog stopped. Goodbye! 👋")


if __name__ == "__main__":
    main()
