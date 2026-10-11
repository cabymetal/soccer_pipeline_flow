"""
Resolve free-text team names (e.g. "barça", "man city") to team rows.
"""
from soccer_bot.text import normalize

# common nicknames -> substring of the official API name (normalized)
NICKNAMES = {
    "barca": "barcelona",
    "real": "real madrid",
    "madrid": "real madrid",
    "atleti": "atletico madrid",
    "atletico": "atletico madrid",
    "man city": "manchester city",
    "city": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
    "united": "manchester united",
    "spurs": "tottenham",
    "nacional": "atletico nacional",
    "america": "america de cali",
    "millos": "millonarios",
}

_NOISE = {"fc", "cf", "sc", "cd", "ud", "afc", "club", "de futbol"}


def _clean(name: str) -> str:
    words = [w for w in normalize(name).split() if w not in _NOISE]
    return " ".join(words)


def find_teams(teams: list[dict], query: str, league_id: int | None = None) -> list[dict]:
    """
    Return the best matching team rows. Exact name matches win over partial ones.
    Each row needs at least: team_id, name, league.
    """
    q = _clean(query)
    q = NICKNAMES.get(q, q)
    if not q:
        return []

    candidates = [t for t in teams if league_id is None or t["league"] == league_id]
    exact = [t for t in candidates if _clean(t["name"]) == q]
    if exact:
        return exact
    return [t for t in candidates if q in _clean(t["name"])]
