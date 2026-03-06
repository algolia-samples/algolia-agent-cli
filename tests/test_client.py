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


def test_list_provider_models(client):
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    with patch("urllib.request.urlopen", return_value=_mock_response(models)):
        result = client.list_provider_models("provider-uuid")
    assert result == models


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


# ── Retry behaviour ───────────────────────────────────────────────────────────

def _http_error(code: int, body: bytes = b"error") -> urllib.error.HTTPError:
    hdrs = MagicMock()
    hdrs.get = MagicMock(return_value=None)  # no Retry-After header
    return urllib.error.HTTPError(
        url="http://example.com", code=code,
        msg="err", hdrs=hdrs, fp=BytesIO(body),
    )


def test_retries_on_429_then_succeeds(client):
    agent = {"id": "abc", "name": "Test", "status": "draft"}
    responses = [_http_error(429), _mock_response({"data": agent})]
    with patch("urllib.request.urlopen", side_effect=responses):
        with patch("time.sleep") as mock_sleep:
            result = client.get_agent("abc")
    assert result == agent
    mock_sleep.assert_called_once()


def test_retries_on_503_then_succeeds(client):
    agent = {"id": "abc", "name": "Test", "status": "draft"}
    responses = [_http_error(503), _http_error(503), _mock_response({"data": agent})]
    with patch("urllib.request.urlopen", side_effect=responses):
        with patch("time.sleep"):
            result = client.get_agent("abc")
    assert result == agent


def test_raises_after_max_retries(client):
    with patch("urllib.request.urlopen", side_effect=_http_error(503)):
        with patch("time.sleep"):
            with pytest.raises(AgentAPIError) as exc_info:
                client.get_agent("abc")
    assert exc_info.value.status_code == 503


def test_no_retry_on_4xx(client):
    """4xx errors (except 429) should fail immediately without retrying."""
    with patch("urllib.request.urlopen", side_effect=_http_error(422)) as mock_urlopen:
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(AgentAPIError) as exc_info:
                client.create_agent({})
    assert exc_info.value.status_code == 422
    assert mock_urlopen.call_count == 1
    mock_sleep.assert_not_called()


def test_retries_on_url_error(client):
    """Network errors should retry then raise AgentAPIError(0, ...)."""
    net_err = urllib.error.URLError("Connection refused")
    agent = {"id": "abc", "name": "Test", "status": "draft"}
    responses = [net_err, _mock_response({"data": agent})]
    with patch("urllib.request.urlopen", side_effect=responses):
        with patch("time.sleep"):
            result = client.get_agent("abc")
    assert result == agent


def test_url_error_exhausted_raises(client):
    net_err = urllib.error.URLError("Connection refused")
    with patch("urllib.request.urlopen", side_effect=net_err):
        with patch("time.sleep"):
            with pytest.raises(AgentAPIError) as exc_info:
                client.list_agents()
    assert exc_info.value.status_code == 0
    assert "Connection error" in str(exc_info.value)


def test_retry_after_header_respected(client):
    """429 with Retry-After header should sleep for the specified duration."""
    hdrs = MagicMock()
    hdrs.get = MagicMock(return_value="5")
    err = urllib.error.HTTPError(
        url="http://example.com", code=429,
        msg="Too Many Requests", hdrs=hdrs, fp=BytesIO(b"rate limited"),
    )
    agent = {"id": "abc", "name": "Test", "status": "draft"}
    with patch("urllib.request.urlopen", side_effect=[err, _mock_response({"data": agent})]):
        with patch("time.sleep") as mock_sleep:
            client.get_agent("abc")
    mock_sleep.assert_called_once_with(5.0)


def test_timeout_error_raises_clear_message(client):
    """Bare TimeoutError (Python 3.11+) surfaces a human-readable AgentAPIError."""
    with patch("urllib.request.urlopen", side_effect=TimeoutError):
        with patch("time.sleep"):
            with pytest.raises(AgentAPIError) as exc_info:
                client.list_agents()
    assert exc_info.value.status_code == 0
    assert "timed out" in str(exc_info.value)


def test_timeout_error_retries(client):
    """TimeoutError is retried _MAX_RETRIES times before giving up."""
    from algolia_agent.client import _MAX_RETRIES
    call_count = 0

    def counting_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise TimeoutError

    with patch("urllib.request.urlopen", side_effect=counting_urlopen):
        with patch("time.sleep"):
            with pytest.raises(AgentAPIError):
                client.list_agents()
    assert call_count == _MAX_RETRIES


def test_timeout_error_retries_then_succeeds(client):
    """A transient TimeoutError followed by a successful response works."""
    agent = {"id": "abc", "name": "Test", "status": "draft"}
    with patch("urllib.request.urlopen", side_effect=[TimeoutError, _mock_response({"data": agent})]):
        with patch("time.sleep"):
            result = client.get_agent("abc")
    assert result == agent
