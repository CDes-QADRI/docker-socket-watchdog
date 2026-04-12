"""
Logger Module — Rich colored console output + file logging.

Provides a centralized logger with:
- Colored terminal output using colorama
- File logging to logs/sentinel.log
- Custom formatting with timestamps and severity icons
"""

import logging
import os
import sys
from datetime import datetime
from colorama import Fore, Back, Style, init as colorama_init

# Initialize colorama for cross-platform color support
colorama_init(autoreset=True)

# ─── Custom Log Formatter (Console) ────────────────────────────────────────────

class SentinelConsoleFormatter(logging.Formatter):
    """Beautiful colored console formatter with severity icons."""

    LEVEL_STYLES = {
        logging.DEBUG:    (Fore.CYAN,    "⚙"),
        logging.INFO:     (Fore.GREEN,   "✔"),
        logging.WARNING:  (Fore.YELLOW,  "⚠"),
        logging.ERROR:    (Fore.RED,     "✖"),
        logging.CRITICAL: (Fore.MAGENTA, "💀"),
    }

    def format(self, record):
        color, icon = self.LEVEL_STYLES.get(record.levelno, (Fore.WHITE, "•"))
        timestamp = datetime.now().strftime("%H:%M:%S")

        dim = Style.DIM
        bright = Style.BRIGHT
        reset = Style.RESET_ALL

        formatted = (
            f"{dim}{Fore.WHITE}│ {timestamp} │{reset} "
            f"{color}{bright}{icon} {record.levelname:<8}{reset} "
            f"{dim}{Fore.WHITE}│{reset} "
            f"{color}{record.getMessage()}{reset}"
        )
        return formatted


# ─── Custom Log Formatter (File) ───────────────────────────────────────────────

class SentinelFileFormatter(logging.Formatter):
    """Clean file formatter without color codes."""

    def format(self, record):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] [{record.levelname:<8}] {record.getMessage()}"


# ─── Logger Setup ──────────────────────────────────────────────────────────────

def setup_logger(name: str = "sentinel", log_level: str = "INFO") -> logging.Logger:
    """
    Create and configure the Sentinel logger.

    Args:
        name: Logger name
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        Configured logging.Logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Avoid duplicate handlers on re-init
    if logger.handlers:
        return logger

    # ── Console Handler ──
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(SentinelConsoleFormatter())
    logger.addHandler(console_handler)

    # ── File Handler ──
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "sentinel.log")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(SentinelFileFormatter())
    logger.addHandler(file_handler)

    return logger


# ─── Pre-configured Logger Instance ───────────────────────────────────────────

log = setup_logger()


# ─── Pretty Banners ───────────────────────────────────────────────────────────

def print_banner():
    """Print the startup banner."""
    banner = f"""
{Fore.CYAN}{Style.BRIGHT}
    ╔══════════════════════════════════════════════════════════════╗
    ║                                                              ║
    ║      {Fore.WHITE}🛡️  DOCKER-SOCKET-WATCHDOG{Fore.CYAN}                            ║
    ║      {Fore.WHITE}   Automated Docker Service Healer{Fore.CYAN}                     ║
    ║                                                              ║
    ║      {Style.DIM}{Fore.CYAN}Monitoring • Detecting • Healing • Alerting{Style.BRIGHT}{Fore.CYAN}          ║
    ║                                                              ║
    ╚══════════════════════════════════════════════════════════════╝
{Style.RESET_ALL}"""
    print(banner)


def print_separator(title: str = ""):
    """Print a visual separator line."""
    if title:
        print(f"\n{Fore.CYAN}{Style.DIM}{'─' * 20} {Style.BRIGHT}{title} {Style.DIM}{'─' * 20}{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}{Style.DIM}{'─' * 60}{Style.RESET_ALL}")


def print_container_status(name: str, status: str, state_color: str = Fore.GREEN):
    """Print a single container's status in a formatted line."""
    print(
        f"  {Fore.WHITE}{Style.DIM}│{Style.RESET_ALL} "
        f"{state_color}{'●'}{Style.RESET_ALL}  "
        f"{Fore.WHITE}{Style.BRIGHT}{name:<30}{Style.RESET_ALL} "
        f"{state_color}{status}{Style.RESET_ALL}"
    )
