"""
Data access contract. Rows are plain dicts keyed by the Databricks column names,
so mock and real sources are interchangeable.
"""
from abc import ABC, abstractmethod


class DataSource(ABC):
    # when True every bot reply is flagged as test data
    is_test_data: bool = False

    @abstractmethod
    def list_teams(self) -> list[dict]:
        """One row per team/league with its latest season: team_id, name, league, season."""

    @abstractmethod
    def team_stats(self, team_id: int, league_id: int) -> dict | None:
        """Latest-season row of silver.teams_statistics for the team."""

    @abstractmethod
    def ranking(self, league_id: int, limit: int = 10) -> list[dict]:
        """Latest-season rows of gold.top_teams_by_win_score ordered by rank."""

    @abstractmethod
    def cards(self, team_id: int, league_id: int) -> dict:
        """{'yellow': row | None, 'red': row | None} from the gold card tables, latest season."""
