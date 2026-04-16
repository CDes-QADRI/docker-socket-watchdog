# 🛡️ docker-socket-watchdog

> **Production-Ready Automated Docker Service Healer & ChatOps Interface**

A Python daemon that directly hooks into the Docker events stream to monitor your containers in real-time. It detects crashes, dead states, Out-Of-Memory (OOM) events, and **resource spikes (CPU/RAM)** with zero latency, and allows you to heal them instantly via an **interactive two-way Discord Bot**—without ever opening an SSH session or terminal.

**Stress-tested with 1000+ containers** — scans 1002 containers in under 4 seconds at 62MB memory.

---

## ✨ Enterprise-Grade Features

### ⚡ Real-Time Event-Driven Architecture
Unlike traditional polling cron jobs, the watchdog establishes a continuous stream with the Docker daemon (`/var/run/docker.sock`). It listens for native `die`, `oom`, and `health_status` events and pushes alerts to Discord the millisecond a container stops unexpectedly.

### 🤖 ChatOps Two-Way Interactive Bot
Say goodbye to manual terminal interventions. When a container crashes, the watchdog sends a rich embed to Discord featuring interactive **[🔄 Restart]** and **[⏭️ Skip]** buttons. You can confidently restart production containers directly from your phone.
- Uses `discord.py` WebSockets (No inbound open ports or webhooks receiver needed).
- Fully asynchronous Event Loop decoupled from synchronous Docker SDK calls using thread pools (`loop.run_in_executor`).
- **All alert types** (crash events, periodic scan issues, resource spikes) include interactive buttons.
- **Persistent cross-session buttons** — buttons remain functional even after bot restarts.
- Buttons work across Discord Desktop, Mobile, and Web clients.
- Graceful fallback to webhook-only mode if bot token is not configured.

### 📊 Resource Monitoring (CPU/RAM Spike Detection)
Don't wait for a crash — get alerted **before** it happens. The watchdog continuously monitors running containers for dangerous resource consumption:
- **RAM Threshold**: Alert when a container exceeds configurable memory usage (default: 90%). Catches memory leaks before OOM kills.
- **CPU Threshold**: Alert when CPU usage spikes (default: 90%).
- **Parallel Stats Collection**: Uses thread pool to fetch container stats concurrently (20x faster with many containers).
- **Consecutive Breaches**: Requires multiple consecutive threshold violations before alerting (default: 2), preventing false alarms from brief spikes.
- **Alert Cooldown**: Configurable cooldown per container (default: 5 minutes) to prevent alert spam.
- **Critical Alerts**: Automatically escalates to CRITICAL severity at 95%+ usage with visual resource bars.
- Sends Discord alerts WITH Restart/Skip buttons so you can act immediately.

### 🔒 Security Hardening
Built with defense-in-depth principles following OWASP guidelines:
- **HMAC-SHA256 Webhook Signing**: Optional cryptographic signing of outbound webhook payloads for integrity verification.
- **Log Sanitization**: Automatic redaction of secrets (API keys, tokens, passwords, PEM keys, connection strings) from log files and Discord alerts.
- **Role-Based Authorization**: Discord button actions restricted to authorized users/roles.
- **Container Name Validation**: Prevents injection attacks through container name parameters.
- **Config Bounds Validation**: All numeric configuration values clamped to safe ranges.
- **Docker API Timeouts**: 30-second timeout guards prevent hanging connections.
- **Rate Limiting**: Per-container cooldown + global burst limiter prevents alert flood DoS.
- **Non-Root Container**: Dockerfile runs as unprivileged `sentinel` user (UID 1000).
- **Error Sanitization**: Stack traces and internal errors are stripped before external exposure.

### 🚀 Scalability & Reliability
Designed to handle thousands of containers without degradation:
- **Bounded Event Queue**: 1000-item queue with graceful overflow handling prevents memory exhaustion.
- **Log Rotation**: RotatingFileHandler (5MB × 3 backups) prevents disk filling.
- **HTTP Connection Pooling**: Persistent `requests.Session` reuses TCP connections for webhook calls.
- **Thread Health Monitoring**: Background watchdog checks all 4 threads every 30 seconds, alerts on thread death.
- **Graceful Error Containment**: Exceptions in event callbacks are caught and logged without crashing the daemon.

### 🛡️ Fallback Background Scans & Healer
A background safety-net runs periodic full-state scans (configurable, default 30 mins) to ensure no misconfigured containers slip through. If multiple containers fail simultaneously, the terminal UI activates a **Numbered Batch System** allowing you to selectively restart multiple specific containers (e.g., `1,3`) or process all of them via batch actions.

### 🐳 Native Dockerization & Self-Healing
You can run the Watchdog itself as a lightweight Docker container. It includes advanced retry/self-healing logic to gracefully wait for the host's Docker daemon if it goes offline or restarts.

---

## 🚀 Quick Start (Dockerized — Recommended)

The easiest way to deploy the watchdog is via Docker.

### 1. Environment Setup
Create a `.env` file from the example:
```bash
cp .env.example .env
```

Edit `.env` to add your Discord credentials:
```env
# Required for periodic scan summaries and real-time fallbacks
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Optional (but recommended) — Enables Interactive [Restart] Buttons
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_CHANNEL_ID=your_discord_channel_id
```

### 2. Run via Docker Compose
```bash
docker compose up -d
```
*The `docker-compose.yml` securely mounts read/write access to `/var/run/docker.sock` required to issue restart commands.*

For maximum security, run with additional Docker flags:
```bash
docker run --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --env-file .env docker-socket-watchdog
```

---

## 🛠️ Local Installation (Virtual Environment)

If you prefer to run it directly on the host machine:

### 1. Install Dependencies
Requires Python 3.9+
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Settings
Edit `config.yaml` to set your monitoring preferences (e.g., watch all or specific named containers):
```yaml
containers:
  watch_mode: "all"          # or "specific"
  specific_names:
    - my_postgres
    - my_redis
```

### 3. Execution Modes
```bash
# Continuous real-time monitoring and Discord Bot activation
./venv/bin/python main.py

# Watch-only mode (No terminal prompts, relies strictly on Discord actions)
./venv/bin/python main.py --watch-only

# Single periodic check (Cron-friendly webhook mode)
./venv/bin/python main.py --once
```

---

## 🧪 Integration Testing Suite

The repository includes a comprehensive integration script `test_sentinel.sh` to verify all 14 execution paths (Start, Stop, Kill, Health checks, and Discord webhook propagation).

```bash
# Start the watchdog in watch-only mode
./venv/bin/python main.py --watch-only &

# Run the test suite
bash test_sentinel.sh
```

---

## 📁 Technical Architecture & Project Structure

```text
docker-socket-watchdog/
├── sentinel/
│   ├── discord_bot.py   # asyncio Discord Gateway client, button interaction handlers & persistent views
│   ├── monitor.py       # Docker container scanner, event stream listener & resource monitor (CPU/RAM)
│   ├── alerter.py       # Discord webhook sender with HMAC signing, rate limiting & connection pooling
│   ├── sanitizer.py     # Regex-based secret redaction engine (API keys, tokens, PEM, connection strings)
│   ├── healer.py        # Terminal UI numbered batch selection logic
│   ├── config.py        # Environment & YAML unified configuration parser with bounds validation
│   └── logger.py        # Colored terminal formatting with log rotation & sanitization filter
├── config.yaml          # Scan interval, resource thresholds & container scoping defaults
├── docker-compose.yml   # 1-click deployment stack
├── Dockerfile           # Python Alpine with non-root user & security hardening
├── main.py              # Main Application Thread Controller (4 threads: main, events, resource monitor, bot)
├── .env.example         # Template for required environment variables
└── test_sentinel.sh     # Bash script mimicking 14 generic container lifecycle events
```

### Multi-Threaded Architecture
```
┌─────────────────────────────────────────────────────────────┐
│                    main.py (Orchestrator)                    │
├──────────┬──────────────┬────────────────┬──────────────────┤
│ Thread 1 │   Thread 2   │    Thread 3    │    Thread 4      │
│  Main    │ Event Stream │ Resource Mon.  │  Discord Bot     │
│  Loop    │  Listener    │  (CPU/RAM)     │  (Gateway WS)    │
│          │              │                │                  │
│ Periodic │ docker.sock  │ docker stats   │ Recv button      │
│ scans &  │ → instant    │ → pre-crash    │ clicks →         │
│ terminal │ Discord      │ alerts at 90%+ │ restart/skip     │
│ prompts  │ alerts       │ RAM/CPU        │ containers       │
└──────────┴──────────────┴────────────────┴──────────────────┘
```

---

## ⚙️ Configuration Reference (config.yaml)

### Sentinel Core Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `check_interval_seconds` | `1800` | Fallback scan safety net interval (seconds) |
| `max_restart_attempts` | `2` | Retry logic count per container restart |
| `restart_timeout` | `30` | Grace period for a container to recover (seconds) |

### Resource Monitoring Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `resource_monitoring.enabled` | `true` | Enable/disable CPU/RAM spike detection |
| `resource_monitoring.check_interval_seconds` | `30` | How often to poll container stats |
| `resource_monitoring.ram_threshold_percent` | `90` | RAM usage % that triggers an alert |
| `resource_monitoring.cpu_threshold_percent` | `90` | CPU usage % that triggers an alert |
| `resource_monitoring.consecutive_breaches` | `2` | Required consecutive threshold violations before alerting |
| `resource_monitoring.alert_cooldown_seconds` | `300` | Cooldown between alerts for the same container |

### Container Filtering
| Setting | Default | Description |
|---------|---------|-------------|
| `watch_mode` | `"all"` | `"all"` or `"specific"` restrictions |
| `specific_names` | `[]` | Exact container names to monitor (when `watch_mode: specific`) |
| `exclude_names` | `[]` | Container names to always ignore |

### Environment Variables (`.env`)
| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_WEBHOOK_URL` | Yes | Discord webhook URL for fallback notifications |
| `DISCORD_BOT_TOKEN` | Recommended | Bot token for interactive buttons (Restart/Skip) |
| `DISCORD_CHANNEL_ID` | Recommended | Channel ID where the bot sends alerts |
| `WEBHOOK_SECRET` | Optional | HMAC-SHA256 secret for signing webhook payloads |

---

## 📜 Legal & License

MIT License — Built over the weekend to solve real-world background daemon crashing anxiety. Contributions are welcome!
