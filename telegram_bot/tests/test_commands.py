import pytest

from soccer_bot import commands
from soccer_bot.commands import CommandError, parse_venue
from soccer_bot.data.mock_source import MockSource
from soccer_bot.leagues import find_league
from soccer_bot.teams import find_teams


@pytest.fixture(scope="module")
def source():
    return MockSource()


@pytest.mark.parametrize("args,expected", [
    (["Barcelona", "visitante"], (["Barcelona"], "away")),
    (["Real", "Madrid", "local"], (["Real", "Madrid"], "home")),
    (["Arsenal"], (["Arsenal"], None)),
    (["Arsenal", "TOTAL"], (["Arsenal"], None)),
])
def test_parse_venue(args, expected):
    assert parse_venue(args) == expected


@pytest.mark.parametrize("query,expected", [
    ("barça", "Barcelona"),
    ("FC Barcelona", "Barcelona"),
    ("atlético madrid", "Atletico Madrid"),
    ("man city", "Manchester City"),
    ("américa", "America de Cali"),
])
def test_find_teams(source, query, expected):
    matches = find_teams(source.list_teams(), query)
    assert [m["name"] for m in matches] == [expected]


def test_find_teams_ambiguous(source):
    names = {m["name"] for m in find_teams(source.list_teams(), "manchester")}
    assert names == {"Manchester City", "Manchester United"}


@pytest.mark.parametrize("query,league_id", [
    ("premier", 39), ("La Liga", 140), ("españa", 140), ("colombia", 239), ("239", 239),
])
def test_find_league(query, league_id):
    assert find_league(query).id == league_id


def test_forma_away(source):
    text = commands.forma(source, ["Barcelona", "visitante"])
    assert "DATOS DE PRUEBA" in text
    assert "Barcelona" in text and "VISITANTE" in text
    assert "Goles a favor" in text and "Últimos 10" in text


def test_forma_total_has_three_columns(source):
    text = commands.forma(source, ["Liverpool"])
    assert "Local" in text and "Visit" in text and "Total" in text


def test_forma_unknown_team(source):
    with pytest.raises(CommandError, match="No encontré"):
        commands.forma(source, ["Equipo", "Inventado"])


def test_forma_ambiguous(source):
    with pytest.raises(CommandError, match="varios equipos"):
        commands.forma(source, ["Manchester"])


def test_comparar(source):
    text = commands.comparar(source, ["Arsenal", "vs", "Chelsea", "local"])
    assert "Arsenal" in text and "Chelsea" in text and "LOCAL" in text


def test_comparar_requires_vs(source):
    with pytest.raises(CommandError, match="Formato"):
        commands.comparar(source, ["Arsenal", "Chelsea"])


def test_ranking_sorted(source):
    rows = source.ranking(140)
    assert [r["rank_by_win_score"] for r in rows] == list(range(1, len(rows) + 1))
    assert "Ranking de forma" in commands.ranking(source, ["laliga"])


def test_tarjetas(source):
    text = commands.tarjetas(source, ["Sevilla"])
    assert "Amarillas" in text and "Rojas" in text


def test_equipos(source):
    text = commands.equipos(source, ["colombia"])
    assert "Millonarios" in text and "Barcelona" not in text


def test_team_names_are_escaped():
    from soccer_bot.formatters import format_ambiguous
    text = format_ambiguous("<b>", [{"name": "A&B <x>", "league": 39}])
    assert "<x>" not in text and "&lt;b&gt;" in text
