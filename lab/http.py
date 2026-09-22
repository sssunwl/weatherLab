"""HTTP client with the project-wide request policy."""

from __future__ import annotations

import time

import requests


USER_AGENT = "weatherLab/2 (+https://github.com/sssunwl/weatherLab)"


class HttpClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def get(self, url: str, *, params: dict | None = None) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(url, params=params, timeout=20)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    # IEM 對連續請求會回 429/503,要等久一點才重試
                    status = getattr(exc.response, "status_code", None)
                    time.sleep((10, 30)[attempt] if status in (429, 503)
                               else 1 + attempt)
        assert last_error is not None
        raise last_error

    def json(self, url: str, *, params: dict | None = None):
        return self.get(url, params=params).json()
