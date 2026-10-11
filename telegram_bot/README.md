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

## Add a league

Add an entry to `LEAGUES` in [soccer_bot/leagues.py](soccer_bot/leagues.py) (API-Football league id)
and make sure the pipeline ingests it.

## Tests

```bash
.venv/bin/python -m pytest
```
