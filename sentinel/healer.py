"""
Healer Module — Container restart logic with user confirmation.

Handles:
- Interactive CLI confirmation before restart
- Numbered container list when multiple containers need attention
- Container restart with timeout
- Retry logic on failure
"""

from docker.errors import DockerException
from colorama import Fore, Style
from sentinel.logger import log
from sentinel.config import Config
from sentinel.monitor import ContainerInfo


class ContainerHealer:
    """Handles container restart with user confirmation."""

    def __init__(self, config: Config):
        self.config = config

    def request_confirmation(self, container_info: ContainerInfo) -> str:
        """
        Ask the user for confirmation before restarting a single container.

        Returns:
            'y' — restart this container
            'n' — skip this container
            'a' — restart all remaining
            's' — skip all remaining
        """
        print()
        print(f"  {Fore.YELLOW}{Style.BRIGHT}┌─────────────────────────────────────────────┐{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}{Style.BRIGHT}│  🔔 RESTART CONFIRMATION REQUIRED           │{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}{Style.BRIGHT}└─────────────────────────────────────────────┘{Style.RESET_ALL}")
        print()
        print(f"  {Fore.WHITE}Container : {Style.BRIGHT}{container_info.name}{Style.RESET_ALL}")
        print(f"  {Fore.WHITE}Image     : {container_info.image}{Style.RESET_ALL}")
        print(f"  {Fore.WHITE}Status    : {Fore.RED}{container_info.status.upper()}{Style.RESET_ALL}")
        print(f"  {Fore.WHITE}Reason    : {container_info.reason}{Style.RESET_ALL}")
        print(f"  {Fore.WHITE}Downtime  : {container_info.downtime}{Style.RESET_ALL}")
        print()
        print(f"  {Fore.CYAN}Options:{Style.RESET_ALL}")
        print(f"    {Fore.GREEN}[y]{Style.RESET_ALL} Yes, restart this container")
        print(f"    {Fore.RED}[n]{Style.RESET_ALL} No, skip this container")
        print(f"    {Fore.GREEN}[a]{Style.RESET_ALL} Restart ALL remaining containers")
        print(f"    {Fore.RED}[s]{Style.RESET_ALL} Skip ALL remaining containers")
        print()

        while True:
            try:
                choice = input(
                    f"  {Fore.YELLOW}{Style.BRIGHT}→ Your choice [y/n/a/s]: {Style.RESET_ALL}"
                ).strip().lower()

                if choice in ("y", "n", "a", "s"):
                    return choice
                else:
                    print(f"  {Fore.RED}  Invalid choice. Please enter y, n, a, or s.{Style.RESET_ALL}")
            except (EOFError, KeyboardInterrupt):
                print(f"\n  {Fore.YELLOW}  Skipping...{Style.RESET_ALL}")
                return "n"

    def request_batch_confirmation(self, container_list: list[ContainerInfo]) -> dict:
        """
        Show a numbered list of problematic containers and let the user
        choose which ones to restart/skip by number.

        Args:
            container_list: List of ContainerInfo objects

        Returns:
            dict: {container_name: 'restart' or 'skip'} for each container
        """
        results = {}

        if len(container_list) == 1:
            # Single container — use simple confirmation
            choice = self.request_confirmation(container_list[0])
            name = container_list[0].name
            if choice in ('y', 'a'):
                results[name] = 'restart'
            else:
                results[name] = 'skip'
            return results

        # ── Multiple containers — show numbered list ──
        print()
        print(f"  {Fore.YELLOW}{Style.BRIGHT}┌──────────────────────────────────────────────────────────┐{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}{Style.BRIGHT}│  🔔 MULTIPLE CONTAINERS NEED ATTENTION ({len(container_list)} found)       │{Style.RESET_ALL}")
        print(f"  {Fore.YELLOW}{Style.BRIGHT}└──────────────────────────────────────────────────────────┘{Style.RESET_ALL}")
        print()

        # Display numbered list
        for idx, info in enumerate(container_list, start=1):
            severity_color = Fore.RED if info.severity == "critical" else Fore.YELLOW
            print(
                f"  {Fore.CYAN}{Style.BRIGHT}  [{idx}]{Style.RESET_ALL}  "
                f"{severity_color}{'●'}{Style.RESET_ALL}  "
                f"{Fore.WHITE}{Style.BRIGHT}{info.name:<28}{Style.RESET_ALL} "
                f"{severity_color}{info.status.upper():<10}{Style.RESET_ALL} "
                f"{Fore.WHITE}{Style.DIM}{info.reason}{Style.RESET_ALL}"
            )
            print(
                f"       "
                f"{Fore.WHITE}{Style.DIM}Image: {info.image}  |  "
                f"Downtime: {info.downtime}  |  "
                f"Exit: {info.exit_code}{Style.RESET_ALL}"
            )
            print()

        # Show options
        print(f"  {Fore.CYAN}{Style.BRIGHT}Options:{Style.RESET_ALL}")
        print(f"    {Fore.GREEN}[numbers]{Style.RESET_ALL}  Restart specific containers (e.g. {Fore.GREEN}1,3{Style.RESET_ALL} or {Fore.GREEN}1 3{Style.RESET_ALL} or {Fore.GREEN}2{Style.RESET_ALL})")
        print(f"    {Fore.GREEN}[a]{Style.RESET_ALL}         Restart ALL containers")
        print(f"    {Fore.RED}[s]{Style.RESET_ALL}         Skip ALL containers")
        print()

        while True:
            try:
                raw = input(
                    f"  {Fore.YELLOW}{Style.BRIGHT}→ Enter numbers, 'a' (all), or 's' (skip): {Style.RESET_ALL}"
                ).strip().lower()

                if not raw:
                    print(f"  {Fore.RED}  Please enter a choice.{Style.RESET_ALL}")
                    continue

                # Restart all
                if raw == 'a':
                    for info in container_list:
                        results[info.name] = 'restart'
                    log.info("User chose: restart ALL containers")
                    return results

                # Skip all
                if raw == 's':
                    for info in container_list:
                        results[info.name] = 'skip'
                    log.info("User chose: skip ALL containers")
                    return results

                # Parse numbers (support: "1,3", "1 3", "1, 3", "1,2,3")
                selected = set()
                # Replace commas with spaces, then split
                parts = raw.replace(',', ' ').split()
                valid = True

                for part in parts:
                    try:
                        num = int(part)
                        if 1 <= num <= len(container_list):
                            selected.add(num)
                        else:
                            print(
                                f"  {Fore.RED}  Invalid number: {num}. "
                                f"Valid range: 1-{len(container_list)}{Style.RESET_ALL}"
                            )
                            valid = False
                            break
                    except ValueError:
                        print(f"  {Fore.RED}  Invalid input: '{part}'. Enter numbers, 'a', or 's'.{Style.RESET_ALL}")
                        valid = False
                        break

                if not valid:
                    continue

                if not selected:
                    print(f"  {Fore.RED}  No valid numbers entered.{Style.RESET_ALL}")
                    continue

                # Build results
                for idx, info in enumerate(container_list, start=1):
                    if idx in selected:
                        results[info.name] = 'restart'
                    else:
                        results[info.name] = 'skip'

                # Show selection summary
                restart_names = [n for n, v in results.items() if v == 'restart']
                skip_names = [n for n, v in results.items() if v == 'skip']

                if restart_names:
                    print(
                        f"  {Fore.GREEN}  ✔ Restarting: "
                        f"{', '.join(restart_names)}{Style.RESET_ALL}"
                    )
                if skip_names:
                    print(
                        f"  {Fore.YELLOW}  ⏭ Skipping: "
                        f"{', '.join(skip_names)}{Style.RESET_ALL}"
                    )
                print()

                return results

            except (EOFError, KeyboardInterrupt):
                print(f"\n  {Fore.YELLOW}  Skipping all...{Style.RESET_ALL}")
                for info in container_list:
                    results[info.name] = 'skip'
                return results

    def restart(self, container_info: ContainerInfo, attempt: int = 1) -> bool:
        """
        Attempt to restart a container.

        Args:
            container_info: The problematic container
            attempt: Current attempt number

        Returns:
            True if restart succeeded, False otherwise
        """
        container = container_info.container
        max_attempts = self.config.max_restart_attempts
        timeout = self.config.restart_timeout

        log.info(
            f"Restarting '{container_info.name}' "
            f"(attempt {attempt}/{max_attempts})..."
        )

        try:
            container.restart(timeout=timeout)

            # Reload and verify
            container.reload()
            new_status = container.status

            if new_status == "running":
                log.info(
                    f"✅ '{container_info.name}' restarted successfully — "
                    f"now running"
                )
                return True
            else:
                log.warning(
                    f"Container '{container_info.name}' status after restart: "
                    f"{new_status} (expected 'running')"
                )

                # Retry if we haven't exceeded max attempts
                if attempt < max_attempts:
                    log.info(f"Retrying restart for '{container_info.name}'...")
                    return self.restart(container_info, attempt + 1)

                log.error(
                    f"❌ Failed to restart '{container_info.name}' after "
                    f"{max_attempts} attempts"
                )
                return False

        except DockerException as e:
            log.error(f"Docker error restarting '{container_info.name}': {e}")

            if attempt < max_attempts:
                log.info(f"Retrying restart for '{container_info.name}'...")
                return self.restart(container_info, attempt + 1)

            log.error(
                f"❌ Giving up on '{container_info.name}' after "
                f"{max_attempts} attempts"
            )
            return False
