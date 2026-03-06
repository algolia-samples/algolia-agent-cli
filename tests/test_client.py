import json
import os
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from algolia_agent.client import AgentAPIError, AlgoliaAgentClient


@pytest.fixture
def client():
    return AlgoliaAgentClient(app_id="TESTAPP", api_key="testapikey")


# ── Credential resolution ────────────────────────────────────────────────────

def test_credentials_from_constructor():
    c = AlgoliaAgentClient(app_id="APP", api_key="KEY")
    assert c.app_id == "APP"
    assert c.api_key == "KEY"


def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("ALGOLIA_APP_ID", "ENVAPP")
    monkeypatch.setenv("ALGOLIA_API_KEY", "ENVKEY")
    c = AlgoliaAgentClient()
    assert c.app_id == "ENVAPP"
    assert c.api_key == "ENVKEY"


def test_credentials_constructor_overrides_env(monkeypatch):
    monkeypatch.setenv("ALGOLIA_APP_ID", "ENVAPP")
    monkeypatch.setenv("ALGOLIA_API_KEY", "ENVKEY")
    c = AlgoliaAgentClient(app_id="CLIAPP", api_key="CLIKEY")
    assert c.app_id == "CLIAPP"
    assert c.api_key == "CLIKEY"


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("ALGOLIA_API_KEY", raising=False)
    with patch("algolia_agent.client.Path.cwd", return_value=MagicMock(
        __truediv__=lambda self, other: MagicMock(exists=lambda: False)
    )):
        with pytest.raises(ValueError, match="Missing Algolia credentials"):
            AlgoliaAgentClient()


def test_credentials_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("ALGOLIA_APP_ID", raising=False)
    monkeypatch.delenv("ALGOLIA_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ALGOLIA_APP_ID=DOTENVAPP\nALGOLIA_API_KEY=DOTENVKEY\n")
    with patch("algolia_agent.client.Path.cwd", return_value=tmp_path):
        c = AlgoliaAgentClient()
    assert c.app_id == "DOTENVAPP"
    assert c.api_key == "DOTENVKEY"


# ── API methods ──────────────────────────────────────────────────────────────

def _mock_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.read.return_value = json.dumps(data).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def test_list_agents(client):
    agents = [{"id": "abc", "name": "Test", "status": "draft"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": agents})):
        result = client.list_agents()
    assert result == agents


def test_get_agent(client):
    agent = {"id": "abc", "name": "Test", "status": "draft"}
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": agent})):
        result = client.get_agent("abc")
    assert result == agent


def test_list_providers(client):
    providers = [{"id": "uuid", "name": "hackathon-gemini"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": providers})):
        result = client.list_providers()
    assert result == providers


def test_resolve_provider_id(client):
    providers = [{"id": "uuid-123", "name": "hackathon-gemini"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": providers})):
        result = client.resolve_provider_id("hackathon-gemini")
    assert result == "uuid-123"


def test_resolve_provider_id_not_found(client):
    providers = [{"id": "uuid-123", "name": "hackathon-gemini"}]
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": providers})):
        with pytest.raises(ValueError, match="Provider 'unknown' not found"):
            client.resolve_provider_id("unknown")


def test_create_agent(client):
    agent = {"id": "new-id", "name": "My Agent", "status": "draft"}
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": agent})):
        result = client.create_agent({"name": "My Agent"})
    assert result == agent


def test_publish_agent(client):
    agent = {"id": "abc", "name": "My Agent", "status": "published"}
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": agent})):
        result = client.publish_agent("abc")
    assert result["status"] == "published"


def test_delete_agent(client):
    with patch("urllib.request.urlopen", return_value=_mock_response({"data": {}})):
        result = client.delete_agent("abc")
    assert result == {}


# ── Error handling ────────────────────────────────────────────────────────────

def test_http_error_raises_agent_api_error(client):
    http_err = urllib.error.HTTPError(
        url="http://example.com", code=404,
        msg="Not Found", hdrs=None,
        fp=BytesIO(b'{"message": "Agent not found"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(AgentAPIError) as exc_info:
            client.get_agent("missing-id")
    assert exc_info.value.status_code == 404


def test_http_401_raises_agent_api_error(client):
    http_err = urllib.error.HTTPError(
        url="http://example.com", code=401,
        msg="Unauthorized", hdrs=None,
        fp=BytesIO(b'{"message": "Invalid API key"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(AgentAPIError) as exc_info:
            client.list_agents()
    assert exc_info.value.status_code == 401
