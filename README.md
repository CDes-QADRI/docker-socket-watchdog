# 🛡️ docker-socket-watchdog

> **Production-Ready Automated Docker Service Healer & ChatOps Interface**

A Python daemon that directly hooks into the Docker events stream to monitor your containers in real-time. It detects crashes, dead states, and Out-Of-Memory (OOM) events with zero latency, and allows you to heal them instantly via an **interactive two-way Discord Bot**—without ever opening an SSH session or terminal.

---

## ✨ Enterprise-Grade Features

### ⚡ Real-Time Event-Driven Architecture
Unlike traditional polling cron jobs, the watchdog establishes a continuous stream with the Docker daemon (`/var/run/docker.sock`). It listens for native `die`, `oom`, and `health_status` events and pushes alerts to Discord the millisecond a container stops unexpectedly.

### 🤖 ChatOps Two-Way Interactive Bot
Say goodbye to manual terminal interventions. When a container crashes, the watchdog sends a rich embed to Discord featuring interactive **[🔄 Restart]** and **[⏭️ Skip]** buttons. You can confidently restart production containers directly from your phone.
- Uses `discord.py` WebSockets (No inbound open ports or webhooks receiver needed).
- Fully asynchronous Event Loop decoupled from synchronous Docker SDK calls using thread pools (`loop.run_in_executor`).

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
│   ├── discord_bot.py   # asyncio Discord Gateway client & button interaction handlers
│   ├── monitor.py       # docker-py wrapper & threaded Event Stream generator
│   ├── alerter.py       # Fallback requests-based Discord webhook sender
│   ├── healer.py        # Terminal UI numbered batch selection logic
│   ├── config.py        # Environment & YAML unified configuration parser
│   └── logger.py        # colorama terminal formatting
├── config.yaml          # Scan interval and container scoping defaults
├── docker-compose.yml   # 1-click deployment stack
├── Dockerfile           # Python Alpine optimization for binary footprints
├── main.py              # Main Application Thread Controller
└── test_sentinel.sh     # Bash script mimicking 14 generic container lifecycle events
```

---

## ⚙️ Configuration Reference (config.yaml)

| Setting | Default | Description |
|---------|---------|-------------|
| `check_interval_seconds` | `1800` | Fallback scan safety net interval (seconds) |
| `max_restart_attempts` | `2` | Retry logic count per container restart |
| `restart_timeout` | `30` | Grace period for a container bounds to recover |
| `watch_mode` | `"all"` | `"all"` or `"specific"` restrictions |
| `specific_names` | `[]` | Exact container names to monitor |

---

## 📜 Legal & License

MIT License — Built over the weekend to solve real-world background daemon crashing anxiety. Contributions are welcome!
