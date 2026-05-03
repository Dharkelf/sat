"""CDSE OAuth token acquisition with automatic refresh."""
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class _TokenInfo:
    access_token: str
    expires_at: float  # epoch seconds


class CDSEAuth:
    """Fetches and caches a CDSE bearer token, refreshing before expiry."""

    def __init__(
        self,
        username: str,
        password: str,
        token_url: str,
        refresh_margin_s: int = 120,
    ) -> None:
        self._username = username
        self._password = password
        self._token_url = token_url
        self._refresh_margin_s = refresh_margin_s
        self._token: _TokenInfo | None = None

    def get_token(self) -> str:
        if self._token is None or time.time() >= self._token.expires_at - self._refresh_margin_s:
            self._token = self._fetch()
        return self._token.access_token

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def _fetch(self) -> _TokenInfo:
        resp = httpx.post(
            self._token_url,
            data={
                "client_id": "cdse-public",
                "username": self._username,
                "password": self._password,
                "grant_type": "password",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        expires_at = time.time() + int(payload["expires_in"])
        logger.info("CDSE token acquired, valid for %ds", payload["expires_in"])
        return _TokenInfo(access_token=payload["access_token"], expires_at=expires_at)
