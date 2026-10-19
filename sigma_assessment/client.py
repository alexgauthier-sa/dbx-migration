"""Small standard-library client for the Sigma REST API."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator


class SigmaAPIError(RuntimeError):
    """A Sigma API request failed."""

    def __init__(self, status: int, method: str, url: str, body: str):
        super().__init__(f"Sigma API returned HTTP {status} for {method} {url}: {body[:500]}")
        self.status = status
        self.method = method
        self.url = url
        self.body = body


@dataclass
class SigmaClient:
    base_url: str
    client_id: str | None = None
    client_secret: str | None = None
    access_token: str | None = None
    timeout: float = 30.0
    max_retries: int = 4

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.access_token and not (self.client_id and self.client_secret):
            raise ValueError("Provide SIGMA_ACCESS_TOKEN or both SIGMA_CLIENT_ID and SIGMA_CLIENT_SECRET")
        self._token_expires_at = float("inf") if self.access_token else 0.0

    def _authenticate(self) -> None:
        data = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id or "",
                "client_secret": self.client_secret or "",
            }
        ).encode()
        url = f"{self.base_url}/v2/auth/token"
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30, 2**attempt + random.random())
                    time.sleep(delay)
                    continue
                raise SigmaAPIError(exc.code, "POST", request.full_url, body) from exc
            except urllib.error.URLError:
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(30, 2**attempt + random.random()))
        else:
            raise AssertionError("authentication retry loop exhausted")
        self.access_token = payload["access_token"]
        self._token_expires_at = time.monotonic() + max(0, int(payload.get("expires_in", 3600)) - 60)

    def request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if time.monotonic() >= self._token_expires_at:
            self._authenticate()
        query = urllib.parse.urlencode(
            {key: str(value).lower() if isinstance(value, bool) else value for key, value in (params or {}).items() if value is not None}
        )
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")

        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                if exc.code == 401 and self.client_id and attempt == 0:
                    self._token_expires_at = 0
                    self._authenticate()
                    continue
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    retry_after = exc.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30, 2**attempt + random.random())
                    time.sleep(delay)
                    continue
                raise SigmaAPIError(exc.code, "GET", url, body) from exc
            except urllib.error.URLError:
                if attempt >= self.max_retries:
                    raise
                time.sleep(min(30, 2**attempt + random.random()))
        raise AssertionError("retry loop exhausted")

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_param: str = "page",
        next_field: str = "nextPage",
        limit_param: str = "limit",
        limit: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        query = dict(params or {})
        query[limit_param] = limit
        seen_tokens: set[str] = set()
        while True:
            payload = self.request(path, query)
            if isinstance(payload, list):
                yield from payload
                return
            yield from payload.get("entries", [])
            token = payload.get(next_field)
            if not token:
                return
            token = str(token)
            if token in seen_tokens:
                raise RuntimeError(f"Pagination token repeated for {path}")
            seen_tokens.add(token)
            query[page_param] = token
