# IRCC Tracker Status Checker → Telegram Bot (via Relay)

Monitor your IRCC application status from inside Iran and get notifications on Telegram through a tiny relay that runs outside Iran.

- **checker** (runs **inside Iran**): talks directly to IRCC/Cognito, never uses VPN.
- **relay** (runs **outside Iran**): receives a POST from the checker and forwards it to the Telegram Bot API.

---

## Contents

- [Features](#features)  
- [Architecture](#architecture)  
- [Prerequisites](#prerequisites)  
- [Repository Structure](#repository-structure)  
- [Setup: Telegram](#setup-telegram)  
- [Environment Variables](#environment-variables)  
- [Local Run](#local-run)  
- [Deployment](#deployment)  
  - [Deploy the Relay (outside Iran)](#deploy-the-relay-outside-iran)  
  - [Run the Checker (inside Iran)](#run-the-checker-inside-iran)  
- [Testing](#testing)  
- [Disclaimer](#disclaimer)

---

## Features

- Polls IRCC Tracker API with Cognito auth (token refresh, early renewal).
- Diffs previous vs current status (`lastUpdated`, `activities`, `history`) and logs changes.
- Sends readable Telegram messages (chunked for 4096-char limit, optional parse mode).
- Atomic state file writes, simple retries, timestamped logs.
- Clean separation: **no Telegram traffic from Iran**; the relay handles it abroad.

---

## Architecture

```
+-------------------+                         +---------------------------+                         +-----------------------+
|  checker (Iran)   |  HTTPS POST (JSON)      |   relay (outside Iran)    |  HTTPS POST             |  Telegram Bot API     |
|  - IRCC calls     +------------------------->+  - Auth via shared secret +------------------------->+  sendMessage          |
|  - Local state    |  X-Hook-Secret header   |  - Forwards to Telegram   |                         |                       |
+-------------------+                         +---------------------------+                         +-----------------------+
```
- **Checker** only talks to IRCC/Cognito and the relay URL you control.  
- **Relay** talks to Telegram, protected by a shared secret.

---

## Prerequisites

- Python **3.10+** on both machines.
- One machine **inside Iran** (for the checker).
- One host/cloud **outside Iran** (for the relay).
- A **Telegram bot** and your **chat ID**.

---

## Repository Structure

```
.
├─ checker/
│  ├─ main.py
│  ├─ .env
│  └─ requirements.txt
└─ relay/
   ├─ main.py
   ├─ .env
   └─ requirements.txt
```

---

## Setup: Telegram

1. In Telegram, talk to **@BotFather** → `/newbot` → follow prompts → copy the **HTTP API token**.
2. Get your **chat ID**:
   - DM your bot, then open: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and copy `result[...].message.chat.id`.
   - For groups: add the bot to the group, send any message, then use `getUpdates`. Group IDs are often negative (e.g., `-100...`).

---

## Environment Variables

Create two `.env` files (one under `relay/`, one under `checker/`).

### `relay/.env` (outside Iran)

```
# Shared secret — must match the checker's TG_RELAY_SECRET
HOOK_SECRET=your-long-random-secret

# Telegram bot credentials
TELEGRAM_BOT_TOKEN=123456789:AA...your_bot_token...
# Target chat (user or group; groups often look like -100XXXXXXXXXXXX)
TELEGRAM_CHAT_ID=123456789

# Optional formatting: Markdown | MarkdownV2 | HTML
TELEGRAM_PARSE_MODE=Markdown

# Optional (platforms like Railway inject PORT automatically)
PORT=8000
```

### `checker/.env` (inside Iran)

```
# IRCC credentials (Cognito)
UCI=your_uci_here
AUTH_PASSWORD=your_password_here

# Relay endpoint (must include scheme http:// or https://)
TG_RELAY_URL=https://your-relay.example.com/
TG_RELAY_SECRET=your-long-random-secret   # exactly the same as HOOK_SECRET

# Polling interval (minutes)
POLL_INTERVAL_MINUTES=5
```

> Generate a strong secret:
> ```bash
> python - <<'PY'
> import secrets; print(secrets.token_urlsafe(32))
> PY
> ```

---

## Local Run

### 1) Relay (outside Iran or locally for testing)

```bash
cd relay
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# Create relay/.env with the values above
uvicorn main:app --host 0.0.0.0 --port 8000
```

You now have a relay listening at `http://localhost:8000/` (or your server’s IP).

### 2) Checker (inside Iran or locally for testing)

```bash
cd checker
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# Create checker/.env with the values above
python main.py
```

The checker will write `previous_state.json` and `changes.log` next to `checker/main.py`.

---

## Deployment

### Deploy the Relay (outside Iran)

- **Koyeb / Railway** 
  ```bash
  # Start command for most platforms:
  uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
  ```
  Set environment variables from `relay/.env`. Use the generated HTTPS URL as `TG_RELAY_URL` on the checker.

- **VPS**
  - Open firewall for your chosen port (default 8000).
  - (Recommended) Put it behind NGINX/Caddy with HTTPS.
  - Run with `systemd`, `pm2`, `screen`, or your process manager of choice.

### Run the Checker (inside Iran)

- Place `checker/` on your Iran machine.
- Ensure `TG_RELAY_URL` is a full URL (including `http://` or `https://`).
- Start `python main.py`. Keep it running with a process manager if desired.

---

## Testing

### Test the Relay Endpoint

From the **checker machine** (or anywhere):

```bash
curl -i -X POST "https://your-relay.example.com/" \
  -H "Content-Type: application/json" \
  -H "X-Hook-Secret: your-long-random-secret" \
  -d '{"text":"ping from curl"}'
```

Expected: `HTTP/1.1 200 OK` and `{"ok": true}`, and a message in your Telegram chat.  
If you get a 502 with a Telegram error, check the bot token or chat ID.

### Test the Checker Flow

- Start `python checker/main.py`.
- Watch the console / `checker/changes.log` for lines like:
  ```
  [2025-10-13 14:20:00] Token refreshed successfully
  [2025-10-13 14:20:01] Initialized data
  [2025-10-13 14:25:01] No changes detected.
  ```
- When IRCC status changes, you’ll receive a Telegram message via the relay.

---

## Disclaimer

This project is not web scraping and it is not official IRCC software. 
The checker calls documented HTTP endpoints exposed by the IRCC Tracker application and authenticates via AWS Cognito using your own credentials, then forwards change notifications to Telegram through a relay you control. 
This code is provided “as is,” without warranty of any kind. It is a personal/educational tool and is not affiliated with, endorsed by, or supported by Immigration, Refugees and Citizenship Canada (IRCC). Use at your own risk and follow all applicable laws and terms of service.
