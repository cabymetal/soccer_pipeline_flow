"""
Command logic, independent from Telegram: takes the text arguments, returns an HTML reply.
"""
import re
from html import escape

from soccer_bot import formatters as fmt
from soccer_bot.data.base import DataSource
from soccer_bot.leagues import find_league
from soccer_bot.teams import find_teams

VENUE_WORDS = {
    "local": "home", "casa": "home", "home": "home",
    "visitante": "away", "visita": "away", "fuera": "away", "away": "away",
    "total": None, "todos": None,
}


class CommandError(Exception):
    """Error whose message is shown to the user as-is."""


def parse_venue(args: list[str]) -> tuple[list[str], str | None]:
    """Strip a trailing venue word from the args."""
    if args and args[-1].lower() in VENUE_WORDS:
        return args[:-1], VENUE_WORDS[args[-1].lower()]
    return args, None


def resolve_team(source: DataSource, query: str) -> dict:
    query = query.strip()
    if not query:
        raise CommandError("Falta el nombre del equipo.")
    matches = find_teams(source.list_teams(), query)
    if not matches:
        raise CommandError(f"No encontré el equipo «{escape(query)}». Usa /equipos &lt;liga&gt; para ver los nombres.")
    if len(matches) > 1:
        raise CommandError(fmt.format_ambiguous(query, matches))
    return matches[0]


def resolve_league(args: list[str]) -> int:
    if not args:
        raise CommandError("Indica la liga. Ver /ligas")
    league = find_league(" ".join(args))
    if not league:
        raise CommandError(f"Liga «{escape(' '.join(args))}» no reconocida. Ver /ligas")
    return league.id


def _with_banner(source: DataSource, text: str) -> str:
    return fmt.TEST_BANNER + text if source.is_test_data else text


def forma(source: DataSource, args: list[str]) -> str:
    args, venue = parse_venue(args)
    team = resolve_team(source, " ".join(args))
    stats = source.team_stats(team["team_id"], team["league"])
    if not stats:
        raise CommandError(f"Sin estadísticas para {team['name']}.")
    return _with_banner(source, fmt.format_form(stats, venue))


def comparar(source: DataSource, args: list[str]) -> str:
    args, venue = parse_venue(args)
    parts = re.split(r"\s+vs\.?\s+|\s+contra\s+", " ".join(args), flags=re.IGNORECASE)
    if len(parts) != 2:
        raise CommandError("Formato: /comparar &lt;equipo&gt; vs &lt;equipo&gt; [local|visitante]")
    team_a, team_b = (resolve_team(source, p) for p in parts)
    stats_a = source.team_stats(team_a["team_id"], team_a["league"])
    stats_b = source.team_stats(team_b["team_id"], team_b["league"])
    if not stats_a or not stats_b:
        raise CommandError("Falta información de alguno de los equipos.")
    return _with_banner(source, fmt.format_compare(stats_a, stats_b, venue))


def ranking(source: DataSource, args: list[str]) -> str:
    league_id = resolve_league(args)
    return _with_banner(source, fmt.format_ranking(source.ranking(league_id, limit=20), league_id))


def tarjetas(source: DataSource, args: list[str]) -> str:
    team = resolve_team(source, " ".join(args))
    cards = source.cards(team["team_id"], team["league"])
    return _with_banner(source, fmt.format_cards(team, cards))


def equipos(source: DataSource, args: list[str]) -> str:
    league_id = resolve_league(args)
    teams = [t for t in source.list_teams() if t["league"] == league_id]
    return _with_banner(source, fmt.format_team_list(teams, league_id))


def ligas(source: DataSource, args: list[str]) -> str:
    return fmt.format_leagues()


def ayuda(source: DataSource, args: list[str]) -> str:
    return _with_banner(source, fmt.HELP)


COMMANDS = {
    "forma": forma,
    "comparar": comparar,
    "ranking": ranking,
    "tarjetas": tarjetas,
    "equipos": equipos,
    "ligas": ligas,
    "ayuda": ayuda,
    "start": ayuda,
    "help": ayuda,
}
