"""Interfaz común para los adaptadores de tienda."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from urllib.robotparser import RobotFileParser

import httpx

from monitor.models import Product


class RobotsDisallowed(Exception):
    """robots.txt de la tienda no permite leer la ruta pedida."""


class StoreAdapter(ABC):
    def __init__(self, store: dict, client: httpx.Client, user_agent: str):
        self.store = store
        self.store_id = store["id"]
        self.base_url = store["url"].rstrip("/")
        self.delay = float(store.get("request_delay", 2))
        self.client = client
        self.user_agent = user_agent
        self._robots: RobotFileParser | None = None

    def check_robots(self, path: str) -> None:
        """Lanza RobotsDisallowed si robots.txt prohíbe `path` a nuestro user agent."""
        if self._robots is None:
            self._robots = RobotFileParser()
            try:
                resp = self.client.get(f"{self.base_url}/robots.txt")
                lines = resp.text.splitlines() if resp.status_code == 200 else []
            except httpx.HTTPError:
                lines = []
            self._robots.parse(lines)
        if not self._robots.can_fetch(self.user_agent, f"{self.base_url}{path}"):
            raise RobotsDisallowed(f"robots.txt de {self.base_url} no permite {path}")

    def get_with_backoff(self, url: str, params: dict | None = None, retries: int = 3) -> httpx.Response:
        """GET que, ante un 429 (demasiadas peticiones), espera y reintenta."""
        for attempt in range(retries + 1):
            resp = self.client.get(url, params=params)
            if resp.status_code != 429 or attempt == retries:
                break
            retry_after = resp.headers.get("Retry-After", "")
            wait = float(retry_after) if retry_after.isdigit() else self.delay * 5 * (attempt + 1)
            time.sleep(wait)
        resp.raise_for_status()
        return resp

    @abstractmethod
    def fetch_products(self) -> list[Product]:
        """Devuelve el catálogo completo de la tienda."""
