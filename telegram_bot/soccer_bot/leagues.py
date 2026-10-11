"""
Supported leagues. To add a league, append a League entry; ids are API-Football league ids.
"""
from dataclasses import dataclass

from soccer_bot.text import normalize


@dataclass(frozen=True)
class League:
    id: int
    name: str
    flag: str
    aliases: tuple[str, ...]


LEAGUES: tuple[League, ...] = (
    League(140, "La Liga", "🇪🇸", ("laliga", "liga", "espana", "spain", "esp")),
    League(39, "Premier League", "🏴󠁧󠁢󠁥󠁮󠁧󠁿", ("premier", "inglaterra", "england", "epl", "eng")),
    League(239, "Primera A", "🇨🇴", ("colombia", "betplay", "primeraa", "dimayor", "col")),
)

_BY_ID = {league.id: league for league in LEAGUES}


def get_league(league_id: int) -> League | None:
    return _BY_ID.get(league_id)


def league_label(league_id: int) -> str:
    league = get_league(league_id)
    return f"{league.flag} {league.name}" if league else f"Liga {league_id}"


def find_league(query: str) -> League | None:
    """Match a league by id, name or alias (accent/case/space insensitive)."""
    q = normalize(query).replace(" ", "")
    if q.isdigit():
        return _BY_ID.get(int(q))
    for league in LEAGUES:
        names = (normalize(league.name).replace(" ", ""), *league.aliases)
        if q in names:
            return league
    return None
