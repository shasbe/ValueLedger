"""HTTP client for the AttributionService."""
from __future__ import annotations

import httpx


class LedgerClient:
    def __init__(self, base_url: str, api_key: str, user_email: str,
                 timeout: float = 120.0):
        self.base = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key, "X-User-Email": user_email,
                        "Content-Type": "application/json"}
        self.http = httpx.Client(timeout=timeout)

    def policy(self) -> dict:
        r = self.http.get(f"{self.base}/v1/policy", headers=self.headers)
        r.raise_for_status()
        return r.json()

    def post_events(self, sessions: list[dict]) -> dict:
        r = self.http.post(f"{self.base}/v1/events",
                           headers=self.headers, json={"sessions": sessions})
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self.http.close()
