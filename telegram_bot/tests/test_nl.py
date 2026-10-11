"""
Natural-language layer tests. Claude is replaced by a fake client: no API calls, no cost.
"""
from types import SimpleNamespace

import pytest

from soccer_bot.data.mock_source import MockSource
from soccer_bot.nl_router import OUT_OF_SCOPE_REPLY, TOOLS, UNSUPPORTED_REPLY, NLRouter


def _response(*blocks, stop_reason="tool_use"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=list(blocks),
        usage=SimpleNamespace(input_tokens=1200, output_tokens=150),
    )


def _tool_use(name, **params):
    return SimpleNamespace(type="tool_use", name=name, input=params)


def _text(text):
    return SimpleNamespace(type="text", text=text)


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.fixture(scope="module")
def source():
    return MockSource()


def route(source, response, question="pregunta"):
    client = FakeClient(response)
    return NLRouter(source, client=client).route(question), client


def test_routes_to_forma_with_data_and_shortcut(source):
    result, client = route(source, _response(_tool_use("forma", equipo="barça", sede="visitante", ultimos_n_pedido=None)))
    assert result.status == "answered" and result.tool == "forma"
    assert "Barcelona" in result.reply and "VISITANTE" in result.reply
    assert "/forma barça visitante" in result.reply
    assert result.input_tokens == 1200 and result.output_tokens == 150
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert "tool_choice" not in call  # forced tool_choice is rejected on Opus 5.5
    assert len(call["messages"]) == 1  # single turn, no history


def test_last_n_request_is_answered_and_logged_for_backlog(source):
    result, _ = route(source, _response(_tool_use("forma", equipo="Barcelona", sede="visitante", ultimos_n_pedido=20)))
    assert result.status == "answered"
    assert "últimos 20" in result.reply
    assert result.category == "ultimos_n_partidos_con_goles"


def test_comparar(source):
    result, _ = route(source, _response(_tool_use("comparar", equipo_a="Arsenal", equipo_b="Chelsea", sede="total")))
    assert result.status == "answered"
    assert "/comparar Arsenal vs Chelsea" in result.reply


def test_unsupported_is_logged_with_category(source):
    result, _ = route(source, _response(_tool_use("no_soportada", categoria="jugadores", resumen="goleador del Barça")))
    assert result.status == "unsupported"
    assert result.reply == UNSUPPORTED_REPLY
    assert (result.category, result.summary) == ("jugadores", "goleador del Barça")


def test_off_topic(source):
    result, _ = route(source, _response(_tool_use("no_soportada", categoria="fuera_de_tema", resumen="receta de pasta")))
    assert result.reply == OUT_OF_SCOPE_REPLY


def test_model_text_is_never_forwarded(source):
    result, _ = route(source, _response(_text("Claro, aquí tienes un poema..."), stop_reason="end_turn"))
    assert result.reply == OUT_OF_SCOPE_REPLY
    assert "poema" not in result.reply


def test_refusal(source):
    result, _ = route(source, _response(stop_reason="refusal"))
    assert result.reply == OUT_OF_SCOPE_REPLY


def test_unknown_team_from_model_goes_through_data_lookup(source):
    result, _ = route(source, _response(_tool_use("forma", equipo="Equipo Inventado", sede="total", ultimos_n_pedido=None)))
    assert result.status == "error"
    assert "No encontré" in result.reply


def test_tools_are_strict():
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


def test_locked_tool_is_not_executed(source):
    result, _ = route_with(source, _response(_tool_use("tarjetas", equipo="Sevilla")), can_use=lambda t: t != "tarjetas")
    assert result.status == "locked" and result.reply == ""


def route_with(source, response, can_use):
    client = FakeClient(response)
    return NLRouter(source, client=client).route("pregunta", can_use), client
