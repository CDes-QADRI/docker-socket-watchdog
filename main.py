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
from sentinel.monitor import DockerMonitor, DockerEventListener, ContainerInfo, ResourceMonitor
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

def run_scan_cycle(monitor, healer, alerter, watch_only=False, bot=None):
    """
    Execute one complete periodic scan cycle:
    1. Scan containers
    2. Display status
    3. Alert on issues (with buttons via bot if available)
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
                # Try bot (with buttons) first, fall back to webhook
                if not bot or not bot.send_issue_alert(info):
                    alerter.send_issue_detected(info)
                actions_taken.append(f"🔔 `{info.name}` — notified (watch-only mode)")
        else:
            # Send Discord alerts for all problematic containers
            for info in problematic:
                # Try bot (with buttons) first, fall back to webhook
                if not bot or not bot.send_issue_alert(info):
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
        run_scan_cycle(monitor, healer, alerter, watch_only=args.watch_only, bot=sentinel_bot)
        log.info("Single scan complete. Exiting.")
    else:
        # ═══════════════════════════════════════════════════════════════════
        # EVENT-DRIVEN ARCHITECTURE
        # ═══════════════════════════════════════════════════════════════════

        # Queue: events thread puts crash events here → main thread processes
        # Bounded to 100 items to prevent memory exhaustion under event storms.
        # If full, new events are dropped (Discord alert already sent by that point).
        event_q = queue.Queue(maxsize=100)

        def on_docker_event(container_event):
            """
            Callback from the events thread (runs in background thread).
            Sends Discord notification IMMEDIATELY, then queues crash events
            for the main thread to handle confirmation.
            """
            try:
                _process_docker_event(container_event)
            except Exception as e:
                # Never let a single bad event crash the listener thread
                log.error(f"Error processing event for '{container_event.container_name}': {e}")

        def _process_docker_event(container_event):
            # Always send instant Discord notification
            # Route through bot when available (buttons for attention events,
            # consistent identity for all events)
            bot_handled = False
            if sentinel_bot:
                if container_event.needs_attention:
                    # Send WITH Restart/Skip buttons
                    sent = sentinel_bot.send_interactive_alert(container_event)
                    if sent:
                        bot_handled = True
                    else:
                        alerter.send_realtime_event(container_event)
                else:
                    # Info events — send through bot without buttons
                    sent = sentinel_bot.send_interactive_alert(container_event)
                    if not sent:
                        alerter.send_realtime_event(container_event)
            else:
                alerter.send_realtime_event(container_event)

            # Log to terminal
            log.info(
                f"{container_event.emoji} "
                f"{container_event.container_name} → "
                f"{container_event.description}"
            )

            # Queue for terminal only if bot didn't handle it (avoids race condition)
            if container_event.needs_attention and not args.watch_only and not bot_handled:
                try:
                    event_q.put_nowait(container_event)
                except queue.Full:
                    log.warning(
                        f"Event queue full — dropping terminal prompt for "
                        f"'{container_event.container_name}' (Discord alert already sent)"
                    )

        # Start the real-time event listener in a daemon thread
        listener = DockerEventListener(config, monitor.client)
        events_thread = threading.Thread(
            target=listener.listen,
            args=(on_docker_event,),
            daemon=True,
        )
        events_thread.start()

        # ── Start Resource Monitor Thread (CPU/RAM spikes) ──
        resource_monitor = None
        if config.resource_monitoring_enabled:
            resource_monitor = ResourceMonitor(config, monitor.client)

            def on_resource_alert(alert):
                """Callback when a container exceeds resource thresholds."""
                try:
                    log.warning(
                        f"{alert.emoji} {alert.container_name} → "
                        f"{alert.description}"
                    )
                    # Send via bot (with buttons) if available, else webhook
                    bot_sent = False
                    if sentinel_bot:
                        bot_sent = sentinel_bot.send_resource_alert(alert)
                    if not bot_sent:
                        alerter.send_resource_alert(alert)
                except Exception as e:
                    log.error(f"Error processing resource alert: {e}")

            resource_thread = threading.Thread(
                target=resource_monitor.run_loop,
                args=(on_resource_alert,),
                daemon=True,
            )
            resource_thread.start()

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
        run_scan_cycle(monitor, healer, alerter, watch_only=args.watch_only, bot=sentinel_bot)

        # ── Main Loop ──
        last_scan_time = time.time()
        last_health_check = time.time()
        _thread_death_alerted = set()  # Track which threads we've already warned about

        while not shutdown_requested:
            # Process any queued crash events (from events thread)
            try:
                event = event_q.get_nowait()
                try:
                    result = handle_problematic_event(event, healer, alerter)
                except Exception as e:
                    log.error(f"Error handling event for '{event.container_name}': {e}")
                    result = None
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
                        except Exception:
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
                        except Exception as e:
                            log.error(f"Error auto-restarting: {e}")
                            break
            except queue.Empty:
                pass
            except Exception as e:
                log.error(f"Unexpected error in main loop: {e}")

            # Check if it's time for a periodic scan
            try:
                if time.time() - last_scan_time >= interval:
                    run_scan_cycle(monitor, healer, alerter, watch_only=args.watch_only, bot=sentinel_bot)
                    last_scan_time = time.time()
            except Exception as e:
                log.error(f"Error during periodic scan: {e}")
                last_scan_time = time.time()

            # ── Thread Health Check (every 30s) ──
            if time.time() - last_health_check >= 30:
                last_health_check = time.time()
                if not events_thread.is_alive() and "events" not in _thread_death_alerted:
                    log.critical("Event listener thread DIED — real-time monitoring lost!")
                    _thread_death_alerted.add("events")
                if resource_monitor and not resource_thread.is_alive() and "resource" not in _thread_death_alerted:
                    log.critical("Resource monitor thread DIED — CPU/RAM monitoring lost!")
                    _thread_death_alerted.add("resource")

            # Sleep briefly before next iteration
            time.sleep(0.5)

        # ── Graceful Shutdown ──
        listener.stop()
        if resource_monitor:
            resource_monitor.stop()

    log.info("Sending shutdown notification...")
    alerter.send_shutdown()
    log.info("docker-socket-watchdog stopped. Goodbye! 👋")


if __name__ == "__main__":
    main()
