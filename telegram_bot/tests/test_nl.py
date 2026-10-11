"""
Natural-language layer tests. Claude is replaced by a fake client: no API calls, no cost.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from soccer_bot.data.mock_source import MockSource
from soccer_bot.nl_router import OUT_OF_SCOPE_REPLY, TOOLS, UNSUPPORTED_REPLY, NLRouter
from soccer_bot.usage import UsageStore


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


# --- quota ---

@pytest.fixture
def store():
    return UsageStore(":memory:", max_questions=3, window_hours=72)


def test_quota_three_per_window(store):
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    statuses = [store.reserve(1, f"q{i}", now=t0 + timedelta(hours=i)) for i in range(4)]
    assert [s.allowed for s in statuses] == [True, True, True, False]
    assert [s.remaining for s in statuses[:3]] == [2, 1, 0]
    assert statuses[3].resets_at == t0 + timedelta(hours=72)


def test_quota_rolls_over(store):
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    for i in range(3):
        store.reserve(1, "q", now=t0 + timedelta(hours=i))
    assert not store.check(1, now=t0 + timedelta(hours=71)).allowed
    assert store.check(1, now=t0 + timedelta(hours=72, minutes=1)).remaining == 1


def test_quota_is_per_user(store):
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    for _ in range(3):
        store.reserve(1, "q", now=t0)
    assert store.reserve(2, "q", now=t0).allowed


def test_refund_returns_the_question(store):
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    ids = [store.reserve(1, "q", now=t0).question_id for _ in range(3)]
    store.refund(ids[-1])
    assert store.check(1, now=t0).remaining == 1


def test_admin_exempt_but_logged(store):
    t0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    assert all(store.reserve(9, "q", exempt=True, now=t0).allowed for _ in range(10))
    assert store.usage_since(36500)["questions"] >= 10


def test_suggestions_aggregate(store):
    qid = store.reserve(1, "¿quién es el goleador del Barça?").question_id
    store.complete(qid, "unsupported", "no_soportada", category="jugadores", summary="goleador del Barça",
                   input_tokens=1000, output_tokens=100)
    qid = store.reserve(1, "forma del Arsenal").question_id
    store.complete(qid, "answered", "forma", {"equipo": "Arsenal"}, input_tokens=1000, output_tokens=100)
    data = store.suggestions()
    assert data["categories"] == [{"category": "jugadores", "n": 1}]
    assert data["tools"] == [{"tool": "forma", "n": 1}]
    usage = store.usage_since(1)
    assert usage["questions"] == 2
    assert usage["cost_usd"] == pytest.approx(2000 * 4e-6 + 200 * 20e-6)
