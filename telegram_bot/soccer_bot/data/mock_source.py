"""
Synthetic data shaped like the real Databricks tables, for local development at zero cost.
Numbers are random (seeded per team) and MUST NOT be used for betting.
"""
import random

from soccer_bot.data.base import DataSource

SEASON_BY_LEAGUE = {140: 2024, 39: 2024, 239: 2025}
GAMES_PER_VENUE = {140: 19, 39: 19, 239: 10}

MOCK_TEAMS = [
    (529, "Barcelona", 140), (541, "Real Madrid", 140), (530, "Atletico Madrid", 140),
    (536, "Sevilla", 140), (543, "Real Betis", 140), (533, "Villarreal", 140),
    (50, "Manchester City", 39), (42, "Arsenal", 39), (40, "Liverpool", 39),
    (33, "Manchester United", 39), (49, "Chelsea", 39), (47, "Tottenham", 39),
    (1137, "Atletico Nacional", 239), (1127, "America de Cali", 239), (1135, "Millonarios", 239),
    (1128, "Deportivo Cali", 239), (1140, "Junior", 239), (1139, "Independiente Medellin", 239),
]


def _split_results(rng: random.Random, played: int, strength: float) -> tuple[int, int, int]:
    wins = round(played * rng.uniform(strength - 0.15, strength + 0.1))
    wins = max(0, min(played, wins))
    draws = rng.randint(0, played - wins)
    return wins, draws, played - wins - draws


def _form_stats(form: str, n: int) -> dict:
    last = form[-n:]
    wins, draws, losses = last.count("W"), last.count("D"), last.count("L")
    streak_type, streak = last[-1], 0
    for result in reversed(last):
        if result != streak_type:
            break
        streak += 1
    p = f"last_{n}_"
    return {
        f"latest_{n}_games": last,
        p + "wins": wins, p + "draws": draws, p + "losses": losses, p + "total": len(last),
        p + "win_ratio": wins / len(last), p + "draw_ratio": draws / len(last),
        p + "loss_ratio": losses / len(last),
        p + "streak_type": streak_type, p + "streak_count": streak,
    }


def _team_row(team_id: int, name: str, league: int) -> dict:
    rng = random.Random(team_id)
    strength = rng.uniform(0.3, 0.7)
    played = GAMES_PER_VENUE[league]
    row = {"team_id": team_id, "name": name, "league": league, "season": SEASON_BY_LEAGUE[league]}

    for venue, bonus in (("home", 0.1), ("away", -0.1)):
        wins, draws, losses = _split_results(rng, played, strength + bonus)
        gf = wins * 2 + draws + rng.randint(0, losses + 2)
        ga = losses * 2 + draws + rng.randint(0, wins // 2 + 1)
        row |= {
            f"fixtures_played_{venue}": played, f"fixtures_wins_{venue}": wins,
            f"fixtures_draws_{venue}": draws, f"fixtures_losses_{venue}": losses,
            f"goals_for_total_{venue}": gf, f"goals_against_total_{venue}": ga,
            f"goals_for_avg_{venue}": f"{gf / played:.1f}",
            f"goals_against_avg_{venue}": f"{ga / played:.1f}",
            f"clean_sheets_{venue}": rng.randint(0, wins),
            f"failed_to_score_{venue}": rng.randint(0, losses),
            f"biggest_wins_{venue}": f"{rng.randint(2, 5)}-{rng.randint(0, 1)}",
            f"biggest_losses_{venue}": f"{rng.randint(0, 1)}-{rng.randint(2, 4)}",
        }

    for metric in ("fixtures_played", "fixtures_wins", "fixtures_draws", "fixtures_losses",
                   "goals_for_total", "goals_against_total", "clean_sheets", "failed_to_score"):
        row[f"{metric}_total"] = row[f"{metric}_home"] + row[f"{metric}_away"]
    row["goals_for_avg_total"] = f"{row['goals_for_total_total'] / (2 * played):.1f}"
    row["goals_against_avg_total"] = f"{row['goals_against_total_total'] / (2 * played):.1f}"

    weights = [strength, 0.25, 1 - strength]
    row["form"] = "".join(rng.choices("WDL", weights=weights, k=2 * played))
    for n in (10, 5, 3):
        row |= _form_stats(row["form"], n)
    return row


def _cards_row(team: dict, color: str) -> dict:
    rng = random.Random(team["team_id"] * (7 if color == "yellow" else 13))
    games = team["fixtures_played_total"]
    if color == "yellow":
        total = rng.randint(games, games * 3)
        prob = min(99.0, 70 + total / games * 8)
    else:
        total = rng.randint(0, 6)
        prob = round(100 * (1 - (1 - total / games) ** 1), 1)
    avg = total / games
    risk = "ALTO" if avg > (2.3 if color == "yellow" else 0.2) else "MEDIO" if avg > (1.6 if color == "yellow" else 0.08) else "BAJO"
    return {
        "season": team["season"], "league": team["league"], "team_id": team["team_id"],
        "team_name": team["name"], "risk_category": risk,
        f"{color}_card_probability_pct": round(prob, 1),
        f"expected_{color}_cards_next_match": round(avg, 2),
        f"avg_{color}_cards_per_game": round(avg, 2),
        "avg_yellow_cards_per_game": round(avg, 2) if color == "yellow" else round(rng.uniform(1.2, 2.8), 2),
        f"peak_{color}_card_period": rng.choice(["31-45", "46-60", "61-75", "76-90"]),
        f"total_{color}_cards": total,
        "fixtures_played_total": games,
        "last_10_games": team["latest_10_games"],
        "last_match_result": team["form"][-1],
    }


class MockSource(DataSource):
    is_test_data = True

    def __init__(self):
        self._rows = {(t, lg): _team_row(t, n, lg) for t, n, lg in MOCK_TEAMS}

    def list_teams(self) -> list[dict]:
        return [{k: r[k] for k in ("team_id", "name", "league", "season")} for r in self._rows.values()]

    def team_stats(self, team_id: int, league_id: int) -> dict | None:
        return self._rows.get((team_id, league_id))

    def ranking(self, league_id: int, limit: int = 10) -> list[dict]:
        rows = []
        for r in self._rows.values():
            if r["league"] != league_id:
                continue
            score = 50 * r["last_10_win_ratio"] + 30 * r["last_5_win_ratio"] + 20 * r["last_3_win_ratio"]
            rows.append({
                "season": r["season"], "league": league_id, "team_id": r["team_id"],
                "team_name": r["name"], "win_score": round(score, 2),
                "last_10_win_ratio": r["last_10_win_ratio"], "last_5_win_ratio": r["last_5_win_ratio"],
                "last_3_win_ratio": r["last_3_win_ratio"], "form": r["form"],
            })
        rows.sort(key=lambda x: x["win_score"], reverse=True)
        for i, row in enumerate(rows, start=1):
            row["rank_by_win_score"] = i
        return rows[:limit]

    def cards(self, team_id: int, league_id: int) -> dict:
        team = self._rows.get((team_id, league_id))
        if not team:
            return {"yellow": None, "red": None}
        return {"yellow": _cards_row(team, "yellow"), "red": _cards_row(team, "red")}
