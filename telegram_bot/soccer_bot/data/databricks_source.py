"""
Reads the soccer catalog through a Databricks SQL Warehouse.

Every query starts the warehouse if it is stopped (billed compute), so results are
cached in memory for CACHE_TTL_SECONDS. The data is refreshed daily, a 1h cache is safe.
"""
import logging
import time

from databricks import sql

from soccer_bot.config import Settings
from soccer_bot.data.base import DataSource

logger = logging.getLogger(__name__)


class DatabricksSource(DataSource):
    def __init__(self, settings: Settings):
        if not settings.databricks_http_path:
            raise ValueError("DATABRICKS_HTTP_PATH is required when DATA_SOURCE=databricks")
        self._settings = settings
        self._cache: dict[tuple, tuple[float, list[dict]]] = {}
        c = settings.catalog
        self._teams_table = f"{c}.silver.teams_statistics"
        self._ranking_table = f"{c}.gold.top_teams_by_win_score"
        self._yellow_table = f"{c}.gold.probable_yellow_cards_by_team_and_league"
        self._red_table = f"{c}.gold.probable_red_cards_by_team_and_league"

    def _connect(self):
        s = self._settings
        if s.databricks_token:
            return sql.connect(
                server_hostname=s.databricks_host.removeprefix("https://"),
                http_path=s.databricks_http_path,
                access_token=s.databricks_token,
            )
        # fall back to a ~/.databrickscfg profile (e.g. OAuth login done with the CLI)
        from databricks.sdk.core import Config
        cfg = Config(profile=s.databricks_profile, host=s.databricks_host)
        return sql.connect(
            server_hostname=cfg.host.removeprefix("https://"),
            http_path=s.databricks_http_path,
            credentials_provider=lambda: cfg.authenticate,
        )

    def _query(self, statement: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        key = (statement, tuple(sorted(params.items())))
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._settings.cache_ttl_seconds:
            return cached[1]

        logger.info("Databricks query: %s %s", statement.split()[0:6], params)
        with self._connect() as conn, conn.cursor() as cursor:
            cursor.execute(statement, params)
            columns = [d[0] for d in cursor.description]
            rows = [dict(zip(columns, r)) for r in cursor.fetchall()]

        self._cache[key] = (time.monotonic(), rows)
        return rows

    def list_teams(self) -> list[dict]:
        return self._query(f"""
            SELECT team_id, max_by(name, season) AS name, league, max(season) AS season
            FROM {self._teams_table}
            GROUP BY team_id, league
        """)

    def team_stats(self, team_id: int, league_id: int) -> dict | None:
        rows = self._query(f"""
            SELECT * FROM {self._teams_table}
            WHERE team_id = :team_id AND league = :league
            ORDER BY season DESC
            LIMIT 1
        """, {"team_id": team_id, "league": league_id})
        return rows[0] if rows else None

    def ranking(self, league_id: int, limit: int = 10) -> list[dict]:
        return self._query(f"""
            SELECT * FROM {self._ranking_table}
            WHERE league = :league
              AND season = (SELECT max(season) FROM {self._ranking_table} WHERE league = :league)
            ORDER BY rank_by_win_score
            LIMIT :limit
        """, {"league": league_id, "limit": limit})

    def _latest_card_row(self, table: str, team_id: int, league_id: int) -> dict | None:
        rows = self._query(f"""
            SELECT * FROM {table}
            WHERE team_id = :team_id AND league = :league
            ORDER BY season DESC
            LIMIT 1
        """, {"team_id": team_id, "league": league_id})
        return rows[0] if rows else None

    def cards(self, team_id: int, league_id: int) -> dict:
        return {
            "yellow": self._latest_card_row(self._yellow_table, team_id, league_id),
            "red": self._latest_card_row(self._red_table, team_id, league_id),
        }
