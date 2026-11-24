# VYN BOT — Telegram Edition (Silver)

A Telegram bot starter that recreates the VYN dashboard with inline buttons and command-based navigation.

## Included
- `/start` and `/menu` dashboard
- Inline navigation for AI Assistant, Code Lab, Research, Creative Studio, Status, Settings, and Help
- `/chat`, `/code`, `/research`, `/design`, `/status`, `/settings`, `/clear`, `/help`
- Optional OpenAI Responses API integration
- Per-user short conversation context (in-memory; resets when the process restarts)

## 1. Create your Telegram bot
1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the bot token. Keep it private.

## 2. Configure
Copy `.env.example` to `.env`, then add your credentials:

```env
BOT_TOKEN=your_telegram_bot_token
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.5
```

`OPENAI_API_KEY` is optional for the dashboard, but required for AI replies. Do not share either token in chat or commit `.env` to source control.

## 3. Install and run
Python 3.11+ recommended.

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\\Scripts\\activate

pip install -r requirements.txt
python bot.py
```

Open your bot in Telegram and send `/start`.

## 4. Set the V logo
Upload `vyn_v_logo.png` as the bot's profile photo using @BotFather:
- `/mybots` → choose your bot → **Edit Bot** → **Edit Botpic**
- Send the logo image.

## Important
- This starter uses polling, so keep the Python process running on a computer or host.
- Research mode does not include live web search.
- For production, add persistent storage (Redis/database), rate limits, user access controls, monitoring, and secure deployment.
- The bot is not deployed by this project archive. You must configure the tokens and run it on your own host.
