#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║      docker-socket-watchdog — Full Integration Test        ║
# ╚══════════════════════════════════════════════════════════════╝
#
# This script tests ALL container events to verify Discord notifications.
# Make sure Sentinel is running in watch-only mode in another terminal:
#   ./venv/bin/python main.py --watch-only
#

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
RESET='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║      🧪 docker-socket-watchdog — Integration Test Suite    ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""

# ── Cleanup any previous test containers ──
echo -e "${YELLOW}🧹 Cleaning up old test containers...${RESET}"
docker rm -f sentinel_test_nginx sentinel_test_crash sentinel_test_quick 2>/dev/null || true
sleep 1

# ═══════════════════════════════════════════════════════════════
# TEST 1: Create & Start a container
# Expected Discord: 📦 "New container created" + 🟢 "Container started"
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}📦 TEST 1: Creating & starting a container (nginx)${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   Expected Discord alerts: 📦 Created + 🟢 Started"
docker run -d --name sentinel_test_nginx nginx:alpine
echo -e "   ${GREEN}✔ Container created and started${RESET}"
sleep 5

# ═══════════════════════════════════════════════════════════════
# TEST 2: Stop a container (graceful stop)
# Expected Discord: 💀 "Container stopped by signal (exit code 143)"
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${RED}🔴 TEST 2: Stopping the container (graceful stop)${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   Expected Discord alert: 💀 Container stopped by signal"
docker stop sentinel_test_nginx
echo -e "   ${GREEN}✔ Container stopped${RESET}"
sleep 5

# ═══════════════════════════════════════════════════════════════
# TEST 3: Start the container again
# Expected Discord: 🟢 "Container started"
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}🟢 TEST 3: Starting the container again${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   Expected Discord alert: 🟢 Container started"
docker start sentinel_test_nginx
echo -e "   ${GREEN}✔ Container started${RESET}"
sleep 5

# ═══════════════════════════════════════════════════════════════
# TEST 4: Force kill (simulate sudden crash)
# Expected Discord: 💀 "Container stopped by signal (exit code 137)"
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${RED}⚡ TEST 4: Force killing container (SIGKILL)${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   Expected Discord alert: 💀 Container stopped by signal (137)"
docker kill sentinel_test_nginx
echo -e "   ${GREEN}✔ Container killed${RESET}"
sleep 5

# ═══════════════════════════════════════════════════════════════
# TEST 5: Simulate application crash (exit code 1)
# Expected Discord: 💀 "Container CRASHED (exit code 1)" — CRITICAL
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${RED}💥 TEST 5: Simulating application crash (exit code 1)${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   Expected Discord alert: 💀 Container CRASHED (exit code 1) — CRITICAL!"
docker run -d --name sentinel_test_crash alpine sh -c "sleep 2 && exit 1"
echo -e "   ${YELLOW}⏳ Waiting for container to crash (3s)...${RESET}"
sleep 5
echo -e "   ${GREEN}✔ Container crashed with exit code 1${RESET}"
sleep 3

# ═══════════════════════════════════════════════════════════════
# TEST 6: Container that exits instantly (exit code 2)
# Expected Discord: 💀 "Container CRASHED (exit code 2)" — CRITICAL
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${RED}💥 TEST 6: Container with instant crash (exit code 2)${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   Expected Discord alert: 💀 Container CRASHED (exit code 2)"
docker run -d --name sentinel_test_quick alpine sh -c "exit 2"
echo -e "   ${GREEN}✔ Container crashed instantly${RESET}"
sleep 5

# ═══════════════════════════════════════════════════════════════
# TEST 7: Remove containers (destroy events)
# Expected Discord: 🗑️ "Container removed" × 3
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${YELLOW}🗑️  TEST 7: Removing all test containers${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   Expected Discord alerts: 🗑️ Container removed × 3"
docker rm -f sentinel_test_nginx
sleep 3
docker rm -f sentinel_test_crash
sleep 3
docker rm -f sentinel_test_quick
echo -e "   ${GREEN}✔ All test containers removed${RESET}"
sleep 3

# ═══════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}${BOLD}║      ✅ ALL TESTS COMPLETE!                                ║${RESET}"
echo -e "${CYAN}${BOLD}║                                                              ║${RESET}"
echo -e "${CYAN}${BOLD}║      Check your Discord channel for notifications:          ║${RESET}"
echo -e "${CYAN}${BOLD}║        📦 Created (×3)                                      ║${RESET}"
echo -e "${CYAN}${BOLD}║        🟢 Started (×3)                                      ║${RESET}"
echo -e "${CYAN}${BOLD}║        💀 Died/Stopped (×4)                                 ║${RESET}"
echo -e "${CYAN}${BOLD}║        🗑️  Removed (×3)                                     ║${RESET}"
echo -e "${CYAN}${BOLD}║                                                              ║${RESET}"
echo -e "${CYAN}${BOLD}║      Total expected Discord messages: ~13                   ║${RESET}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
