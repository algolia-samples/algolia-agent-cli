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

    def _request(self, path: str, method: str = "GET", body: dict | None = None) -> dict | list:
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
            except TimeoutError:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise AgentAPIError(0, f"Request timed out after {_TIMEOUT}s") from None

    def _paginated(self, path: str) -> list:
        """Collect every page of a list endpoint.

        These endpoints wrap results as {"data": [...], "pagination": {...}} and cap a
        page at 10 items, so reading only the first response silently truncates — an
        account with 14 agents reported 10. Responses without a "pagination" block are
        treated as complete.

        The first request goes to `path` untouched, so it is identical to a plain
        _request() call; `page=` is only added when a second page is actually needed.
        That keeps the helper safe on endpoints that may not accept the parameter.

        Returns list[dict] for the paginated endpoints, but passes a bare-list response
        straight through, hence the unparameterised annotation.
        """
        items: list = []
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            result = self._request(path if page == 1 else f"{path}{sep}page={page}")
            if not isinstance(result, dict):
                # Endpoint returned a bare list; nothing to page through.
                return result if isinstance(result, list) else items
            page_items = result.get("data", [])
            items += page_items
            total_pages = (result.get("pagination") or {}).get("totalPages") or 1
            # `not page_items` guards against a totalPages that never resolves.
            if page >= total_pages or not page_items:
                return items
            page += 1

    def list_agents(self) -> list[dict]:
        return self._paginated("/agents")

    def get_agent(self, agent_id: str) -> dict:
        result = self._request(f"/agents/{agent_id}")
        return result.get("data", result)

    def list_providers(self) -> list[dict]:
        # Same paginated envelope as /agents; only 3 providers today, but the cap is
        # the same 10.
        return self._paginated("/providers")

    def list_indices(self) -> list[str]:
        """Return all index names for this application via the Algolia Search API."""
        url = f"https://{self.app_id}.algolia.net/1/indexes"
        req = urllib.request.Request(url)
        req.add_header("x-algolia-application-id", self.app_id)
        req.add_header("x-algolia-api-key", self.api_key)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "algolia-agent-cli/0.1.0")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                result = json.loads(resp.read())
                return [item["name"] for item in result.get("items", [])]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            return []

    def list_provider_models(self, provider_id: str) -> list[str]:
        result = self._request(f"/providers/{provider_id}/models")
        # Endpoint returns a plain JSON array, not a wrapped {"data": [...]}
        if isinstance(result, list):
            return result
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
        result = self._request(f"/agents/{agent_id}", method="PATCH", body=payload)
        return result.get("data", result)

    def publish_agent(self, agent_id: str) -> dict:
        result = self._request(f"/agents/{agent_id}/publish", method="POST")
        return result.get("data", result)

    def delete_agent(self, agent_id: str) -> dict:
        result = self._request(f"/agents/{agent_id}", method="DELETE")
        return result.get("data", result)
