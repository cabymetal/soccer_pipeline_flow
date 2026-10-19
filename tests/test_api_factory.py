import pytest

from api_factory import APIFactory, SoccerAPIFactory
from api_clients.base_api_consumer import base_api_consumer
from api_clients.soccer_consumer import soccer_consumer


def test_soccer_factory_creates_soccer_consumer():
    client = SoccerAPIFactory(api_key="k").create_api_client()

    assert isinstance(client, soccer_consumer)
    assert isinstance(client, base_api_consumer)
    assert client.api_key == "k"


def test_api_factory_is_abstract():
    with pytest.raises(TypeError):
        APIFactory()


def test_base_api_consumer_is_abstract():
    with pytest.raises(TypeError):
        base_api_consumer()


def test_spotify_stub_imports_without_dotenv():
    from api_clients.spotify_consumer import spotify_consumer

    assert spotify_consumer().fetch_data("me") == {}
