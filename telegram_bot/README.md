# Soccer Stats Telegram Bot (MVP)

Telegram bot that answers soccer stats commands from the `soccer` Databricks catalog,
to support pre-match analysis.

```
Telegram ──► bot (python-telegram-bot, polling) ──► DataSource
                                                     ├── MockSource        (synthetic, free)
                                                     └── DatabricksSource  (SQL Warehouse)
                                                           soccer.silver.teams_statistics
                                                           soccer.gold.top_teams_by_win_score
                                                           soccer.gold.probable_{yellow,red}_cards_by_team_and_league
```

## Commands

| Command | Example | Source |
|---|---|---|
| `/forma <team> [local\|visitante]` | `/forma Barcelona visitante` | silver.teams_statistics |
| `/comparar <team> vs <team> [local\|visitante]` | `/comparar Arsenal vs Chelsea` | silver.teams_statistics |
| `/ranking <league>` | `/ranking premier` | gold.top_teams_by_win_score |
| `/tarjetas <team>` | `/tarjetas Sevilla` | gold card tables |
| `/equipos <league>` | `/equipos colombia` | silver.teams_statistics |
| `/ligas`, `/ayuda` | | |

Stats are **per season**. "Last N matches" with goals and home/away split needs a
per-match `fixtures` table (next phase).

## Run locally

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy the token.
2. Set up the environment:
   ```bash
   cd telegram_bot
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env   # paste TELEGRAM_BOT_TOKEN
   ```
3. Start it and send `/ayuda` to your bot:
   ```bash
   .venv/bin/python -m soccer_bot.main
   ```
   The first time it replies with your `chat_id`. Put it in `ALLOWED_CHAT_IDS` and restart.

With `DATA_SOURCE=mock` every reply is flagged as **test data**.

## Switch to Databricks

Requires `SELECT` on `soccer.silver` and `soccer.gold`, and a CLI profile
(`databricks auth login --profile soccer`) or a token. Set `DATA_SOURCE=databricks`.

Each query wakes the SQL Warehouse if stopped (billed). Results are cached in memory for
`CACHE_TTL_SECONDS` (default 1h) since data is refreshed daily.

## Natural-language questions (Claude)

Any non-command message is sent to Claude (`claude-opus-5-5`), which **only picks one of the
fixed commands** and its parameters. The reply always comes from the fixed command (i.e. from the
data source); Claude's own text is never shown.

- Off by default: `NL_ENABLED=true` + `ANTHROPIC_API_KEY` (Claude Console API key, billed per use).
- Questions are charged to the user's plan (see below). Admins (`ADMIN_CHAT_IDS`) are exempt.
  Failed calls are refunded.
- Guardrails: single turn (no history), `NL_MAX_CHARS` input cap, strict tool schemas, off-topic
  questions get a fixed reply, refusals use server-side fallbacks (`fallbacks: "default"`).
- Every question is logged in a local SQLite file (`DB_PATH`). Admin commands:
  - `/sugerencias`: what users ask that no command covers yet (backlog for new fixed commands).
  - `/uso`: subscribers, MRR, revenue, questions, tokens and Claude cost (last 30 days).
- Each answer shows the equivalent free command, so users learn to skip the LLM.

## Plans and billing

Prices, quotas, gated commands and plan features live in [plans.json](plans.json).

| Tier | Price (COP) | Commands | Free-text questions |
|---|---|---|---|
| Free | 0 | /forma, /ranking, /equipos | 3 welcome + 1 per week |
| Pro | 14.900 / 30 days | all | 50 per period |
| VIP | 29.900 / 30 days | all | 200 per period, max 15/day |
| Founder offer | Pro at 9.900, first 50 users | all | as Pro |
| Pack | 4.900 | — | +15, no expiry |

Questions are charged in this order: plan allowance, then packs, then the free allowance.

User commands: `/planes` (with buy buttons), `/micuenta`.

Payments are **manual** for now: the buy button sends the user `PAYMENT_INSTRUCTIONS` and notifies
admins with the exact activation command. Admin commands:

- `/activar <chat_id> <plan> [periods] [fundador]` (renewals stack after the current period)
- `/recargar <chat_id> <pack>`, `/desactivar <chat_id>`, `/suscriptores`

Subscribers get a renewal reminder `reminder_days_before` days before expiry.
All state lives in the local SQLite file `DB_PATH` (back it up).

## Add a league

Add an entry to `LEAGUES` in [soccer_bot/leagues.py](soccer_bot/leagues.py) (API-Football league id)
and make sure the pipeline ingests it.

## Tests

```bash
.venv/bin/python -m pytest
```
