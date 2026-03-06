"""
HTTP client for the Algolia Agent Studio REST API.

Credential resolution order:
  1. Constructor arguments (app_id, api_key)
  2. Environment variables (ALGOLIA_APP_ID, ALGOLIA_API_KEY)
  3. .env file in the current working directory
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

_TIMEOUT = 30          # seconds per request
_MAX_RETRIES = 3       # attempts total (1 initial + 2 retries)
_RETRY_BACKOFF = 1.0   # seconds; doubles each retry


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file and return key/value pairs. Ignores comments and blank lines."""
    result = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


class AgentAPIError(Exception):
    """Raised when the Agent Studio API returns an error response."""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class AlgoliaAgentClient:
    BASE_PATH = "/agent-studio/1"

    def __init__(self, app_id: str | None = None, api_key: str | None = None):
        env = _load_dotenv(Path.cwd() / ".env")

        self.app_id = app_id or os.getenv("ALGOLIA_APP_ID") or env.get("ALGOLIA_APP_ID")
        self.api_key = api_key or os.getenv("ALGOLIA_API_KEY") or env.get("ALGOLIA_API_KEY")

        if not self.app_id or not self.api_key:
            raise ValueError(
                "Missing Algolia credentials. Provide --app-id/--api-key, "
                "set ALGOLIA_APP_ID/ALGOLIA_API_KEY env vars, or add them to .env"
            )

        self.base_url = f"https://{self.app_id}.algolia.net{self.BASE_PATH}"

    def _request(self, path: str, method: str = "GET", body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("x-algolia-application-id", self.app_id)
        req.add_header("x-algolia-api-key", self.api_key)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "algolia-agent-cli/0.1.0")
        delay = _RETRY_BACKOFF
        for attempt in range(_MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    body = resp.read()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                status = e.code
                # Retry on rate limit or server error, bail immediately on anything else
                if status in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                    wait = delay
                    try:
                        header_val = e.headers and e.headers.get("Retry-After")
                        if header_val:
                            wait = float(header_val)
                    except (ValueError, TypeError):
                        pass
                    time.sleep(wait)
                    delay *= 2
                    continue
                raise AgentAPIError(status, e.read().decode(errors="replace")) from e
            except urllib.error.URLError as e:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise AgentAPIError(0, f"Connection error: {e.reason}") from e

    def list_agents(self) -> list[dict]:
        result = self._request("/agents")
        return result.get("data", [])

    def get_agent(self, agent_id: str) -> dict:
        result = self._request(f"/agents/{agent_id}")
        return result.get("data", result)

    def list_providers(self) -> list[dict]:
        result = self._request("/providers")
        return result.get("data", [])

    def resolve_provider_id(self, provider_name: str) -> str:
        """Resolve a provider name (e.g. 'hackathon-gemini') to its UUID."""
        providers = self.list_providers()
        for provider in providers:
            if provider["name"] == provider_name:
                return provider["id"]
        available = [p["name"] for p in providers]
        raise ValueError(
            f"Provider '{provider_name}' not found. Available: {', '.join(available)}"
        )

    def create_agent(self, payload: dict) -> dict:
        result = self._request("/agents", method="POST", body=payload)
        return result.get("data", result)

    def update_agent(self, agent_id: str, payload: dict) -> dict:
        result = self._request(f"/agents/{agent_id}", method="PUT", body=payload)
        return result.get("data", result)

    def publish_agent(self, agent_id: str) -> dict:
        result = self._request(f"/agents/{agent_id}/publish", method="POST")
        return result.get("data", result)

    def delete_agent(self, agent_id: str) -> dict:
        result = self._request(f"/agents/{agent_id}", method="DELETE")
        return result.get("data", result)
