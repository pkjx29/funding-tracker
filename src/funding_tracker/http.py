"""Polite HTTP access for the collectors.

Every request in this project goes through :class:`Fetcher`, which

* identifies itself with a descriptive User-Agent,
* obeys robots.txt, including ``*`` / ``$`` wildcards and ``Crawl-delay``,
* waits between requests to the same host, and
* caches responses on disk, so a re-run never downloads the same page twice.

If a site answers 401/403 or serves a bot challenge, the page is recorded as
unavailable and skipped. Nothing here tries to get around a block.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

USER_AGENT = "funding-tracker/1.0 (+https://github.com/pkjx29/funding-tracker)"
UA_TOKEN = "funding-tracker"
DEFAULT_DELAY = 2.0   # seconds between two requests to the same host
HOST_DELAY = {"entrackr.com": 1.5}   # per-host overrides; a robots.txt Crawl-delay always wins if longer
TIMEOUT = 30          # seconds per request
MAX_TRIES = 3


class RobotsDisallowed(Exception):
    """Raised when robots.txt does not allow the requested URL."""


def _decode(response: requests.Response) -> str:
    """Decode a response body, preferring UTF-8 when the server names no charset.

    ``requests`` falls back to Latin-1 for ``text/*`` without a charset, which
    garbles the rupee sign and curly quotes that funding headlines are full of.
    """
    raw = response.content
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    if "charset" in response.headers.get("Content-Type", "").lower():
        return response.text
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(response.apparent_encoding or "latin-1", errors="replace")


class RobotsRules:
    """A small robots.txt matcher.

    The standard library parser ignores ``*`` and ``$`` in paths, and three of
    the four sources rely on them, so the rules are matched here the way the
    Robots Exclusion Protocol (RFC 9309) describes: the longest matching
    pattern wins, and ``Allow`` wins a tie.
    """

    def __init__(self, text: str, ua_token: str = UA_TOKEN):
        groups = self._parse(text)
        lines = groups.get(ua_token.lower()) or groups.get("*") or []
        self.rules: list[tuple[bool, str, re.Pattern[str]]] = []
        self.crawl_delay: float | None = None
        for key, value in lines:
            if key == "crawl-delay":
                try:
                    self.crawl_delay = float(value)
                except ValueError:
                    pass
            elif key in ("allow", "disallow") and value:
                self.rules.append((key == "allow", value, self._to_regex(value)))

    @staticmethod
    def _parse(text: str) -> dict[str, list[tuple[str, str]]]:
        """Return {user-agent: [(directive, value), ...]}, merging repeated groups."""
        groups: dict[str, list[tuple[str, str]]] = {}
        agents: list[str] = []
        reading_agents = False
        text = text.lstrip("﻿").removeprefix("ï»¿")   # byte-order mark, decoded either way
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not reading_agents:
                    agents = []
                agents.append(value.lower())
                groups.setdefault(value.lower(), [])
                reading_agents = True
            else:
                reading_agents = False
                for agent in agents:
                    groups[agent].append((key, value))
        return groups

    @staticmethod
    def _to_regex(pattern: str) -> re.Pattern[str]:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
        return re.compile(regex + ("$" if anchored else ""))

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best_len, best_allow = -1, True
        for allow, raw, regex in self.rules:
            if regex.match(target) and (len(raw) > best_len or (len(raw) == best_len and allow)):
                best_len, best_allow = len(raw), allow
        return best_allow


class Fetcher:
    """Fetch pages politely and keep them in an on-disk cache."""

    def __init__(self, cache_dir: Path | str, delay: float = DEFAULT_DELAY, offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.delay = delay
        self.offline = offline            # offline=True: serve from cache only
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        self._robots: dict[str, RobotsRules] = {}
        self._last_hit: dict[str, float] = {}
        self.requests_made = 0

    # ---------------------------------------------------------------- cache
    def _paths(self, url: str) -> tuple[Path, Path]:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        folder = self.cache_dir / urlsplit(url).netloc / digest[:2]
        return folder / f"{digest}.body.gz", folder / f"{digest}.json"

    def cached(self, url: str) -> bool:
        return self._paths(url)[1].exists()

    def _read_cache(self, url: str) -> tuple[bool, str | None]:
        body, meta = self._paths(url)
        if not meta.exists():
            return False, None
        info = json.loads(meta.read_text(encoding="utf-8"))
        if info.get("status") != 200:
            return True, None
        with gzip.open(body, "rt", encoding="utf-8", errors="replace") as handle:
            return True, handle.read()

    def _write_cache(self, url: str, status: int, text: str | None) -> None:
        body, meta = self._paths(url)
        meta.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            with gzip.open(body, "wt", encoding="utf-8") as handle:
                handle.write(text)
        info = {"url": url, "status": status, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        meta.write_text(json.dumps(info), encoding="utf-8")

    # --------------------------------------------------------------- robots
    def robots(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            robots_url = f"{parts.scheme}://{host}/robots.txt"
            hit, text = self._read_cache(robots_url)
            if not hit and not self.offline:
                text = self._download(robots_url)
            self._robots[host] = RobotsRules(text or "")
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        return self.robots(url).allowed(url)

    # ------------------------------------------------------------- download
    def _wait(self, host: str) -> None:
        rules = self._robots.get(host)
        pause = max(HOST_DELAY.get(host, self.delay), (rules.crawl_delay or 0) if rules else 0)
        elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
        if elapsed < pause:
            time.sleep(pause - elapsed)

    def _download(self, url: str) -> str | None:
        host = urlsplit(url).netloc
        status, text = 0, None
        for attempt in range(1, MAX_TRIES + 1):
            self._wait(host)
            try:
                response = self.session.get(url, timeout=TIMEOUT)
            except requests.RequestException as exc:
                log.warning("request failed (%s/%s) %s: %s", attempt, MAX_TRIES, url, exc)
                self._last_hit[host] = time.monotonic()
                time.sleep(5 * attempt)
                continue
            self._last_hit[host] = time.monotonic()
            self.requests_made += 1
            status = response.status_code
            if status == 200:
                text = _decode(response)
                break
            if status in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(int(retry_after) if retry_after.isdigit() else 15 * attempt)
                continue
            log.warning("HTTP %s for %s", status, url)
            break
        if status:                        # do not cache pure network failures
            self._write_cache(url, status, text)
        return text

    def get(self, url: str, refresh: bool = False) -> str | None:
        """Return the page text, or None if it is unavailable.

        ``refresh=True`` re-downloads even when a cached copy exists (used for
        listing pages and sitemaps, which change between runs).
        """
        if not refresh or self.offline:
            hit, text = self._read_cache(url)
            if hit or self.offline:
                return text
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        return self._download(url)
