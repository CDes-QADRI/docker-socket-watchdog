# 🛡️ docker-socket-watchdog

**Automated Docker Service Healer** — A Python daemon that monitors your Docker containers, detects crashes and unhealthy states, and heals them with your confirmation via Discord alerts.

---

## ✨ Features

- 🔍 **Smart Detection** — Monitors containers for `exited`, `dead`, `unhealthy`, and `OOM-killed` states
- 🔔 **Beautiful Discord Alerts** — Rich embed notifications with color-coded severity, diagnostics, and health bars
- ✋ **User Confirmation** — Never auto-restarts! Sends alert first, then asks YOU before taking action
- 🔄 **Auto-Heal** — Restarts problematic containers with retry logic
- ⚙️ **Flexible Config** — Monitor all containers or specific ones, via `config.yaml`
- 📊 **Scan Summaries** — After each scan cycle, get a Discord summary with health percentage
- 🎨 **Beautiful CLI** — Colored terminal output with status icons

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Edit `config.yaml` to set your preferences:
```yaml
containers:
  watch_mode: "all"          # or "specific"
  specific_names:
    - my_postgres
    - my_redis
```

### 3. Set Discord Webhook

Create a `.env` file (copy from `.env.example`):
```bash
cp .env.example .env
```

Edit `.env` and add your Discord webhook:
```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your_webhook_url
```

**How to get a webhook URL:**
1. Open your Discord server → Channel Settings ⚙️
2. Go to **Integrations** → **Webhooks**
3. Click **New Webhook** → **Copy Webhook URL**

### 4. Run

```bash
# Continuous monitoring (every 5 minutes)
python main.py

# Single scan
python main.py --once

# Custom interval (e.g., every 60 seconds)
python main.py --interval 60
```

---

## 🧪 Test It

```bash
# 1. Run a test container
docker run -d --name test_sentinel nginx

# 2. Stop it (simulate crash)
docker stop test_sentinel

# 3. Start Sentinel with single scan
python main.py --once

# 4. See the Discord alert & confirm restart in terminal

# 5. Clean up
docker rm test_sentinel
```

---

## 📁 Project Structure

```
docker-socket-watchdog/
├── sentinel/
│   ├── __init__.py      # Package metadata
│   ├── config.py        # Configuration loader
│   ├── monitor.py       # Docker health checker
│   ├── healer.py        # Restart logic + user confirmation
│   ├── alerter.py       # Discord embed notifications
│   └── logger.py        # Colored logging
├── config.yaml          # Your settings
├── .env                 # Your secrets (gitignored)
├── .env.example         # Template for .env
├── main.py              # Entry point
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

---

## ⚙️ Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `check_interval_seconds` | `300` | How often to scan (seconds) |
| `max_restart_attempts` | `2` | Retry count per container |
| `restart_timeout` | `30` | Docker restart timeout (seconds) |
| `watch_mode` | `"all"` | `"all"` or `"specific"` |
| `specific_names` | `[]` | Container names to watch (when mode=specific) |
| `exclude_names` | `[]` | Containers to always ignore |

---

## 📜 License

MIT — Built as a weekend project with ❤️
