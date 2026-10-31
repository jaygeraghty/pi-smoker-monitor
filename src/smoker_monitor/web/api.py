"""What the web server does for each address. No networking in here.

`WebApp.handle` takes a request (method, path, headers, body, who sent it) and
returns a `Response`, so every rule can be tested without starting a server.

Addresses:
    GET  /                 the page            (static files: see STATIC)
    GET  /api/settings     the settings, their allowed ranges, the revision
    PUT  /api/settings     save changes (refused if someone saved since)
    GET  /api/status       the monitor's latest status.json, with its age
    POST /api/silence      silence what's ringing
    POST /api/end-cook     clear your own probe targets, forget probes in use

Errors are plain JSON: {"error": "..."} or {"errors": {"field": "message"}}.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smoker_monitor.domain.alarms import AlarmRules
from smoker_monitor.service import settings as store
from smoker_monitor.service.monitor import request_end_cook, request_silence
from smoker_monitor.service.settings import (
    ConflictError,
    Settings,
    SettingsError,
    ValidationError,
)
from smoker_monitor.service.status import STATUS_FILE
from smoker_monitor.web.security import client_is_local, host_is_allowed, same_origin

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Address -> (file in static/, content type). Only these are ever served, so
# nothing else on the Pi can be reached through the page.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}

# Typical finished temperatures, offered as one-tap choices for a probe's target.
PRESETS = (
    {"name": "Brisket", "c": 93},
    {"name": "Pork shoulder", "c": 95},
    {"name": "Chicken", "c": 74},
)

# A status file older than this means the monitor isn't running.
MONITOR_STALE_SECONDS = 120

MAX_BODY_BYTES = 64 * 1024

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str = "application/json"
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WebInfo:
    """What the page needs to know from config.toml (never any secrets)."""

    base_rules: AlarmRules
    has_eti: bool = True
    has_traeger: bool = False


def json_response(status: int, data: Any) -> Response:
    return Response(status, (json.dumps(data) + "\n").encode("utf-8"), "application/json")


def error(status: int, message: str) -> Response:
    return json_response(status, {"error": message})


def utc_now() -> datetime:
    return datetime.now(UTC)


class WebApp:
    """The page's brains. `handle` is the only entry point."""

    def __init__(
        self,
        state_dir: Path,
        info: WebInfo,
        extra_hosts: Iterable[str] = (),
        clock: Callable[[], datetime] = utc_now,
        run_dir: Path | None = None,
    ) -> None:
        self.state_dir = state_dir  # settings.json
        self.run_dir = run_dir or state_dir  # status.json and the monitor's request files
        self.info = info
        self.extra_hosts = tuple(extra_hosts)
        self.clock = clock
        self.settings_path = state_dir / store.SETTINGS_FILE

    # ----------------------------------------------------------- routing ---

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        client: str,
        declared_length: int | None = None,
    ) -> Response:
        """Answer one request. Never raises: a bug becomes a plain "500" reply.

        `declared_length` is the Content-Length the sender claimed, when the
        server refused to read a body that big (so `body` is empty).
        """
        try:
            response = self._route(
                method, path.split("?", 1)[0], headers, body, client, declared_length
            )
        except Exception:
            log.exception("Web request failed unexpectedly")
            response = error(500, "Something went wrong on the Pi. See its log.")
        cache = "no-store" if path.startswith("/api/") else "no-cache"
        return Response(
            response.status,
            response.body,
            response.content_type,
            {**SECURITY_HEADERS, "Cache-Control": cache, **response.headers},
        )

    def _route(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        client: str,
        declared_length: int | None,
    ) -> Response:
        lowered = {k.lower(): v for k, v in headers.items()}
        if not client_is_local(client):
            return error(403, "This page is only available on your home network.")
        if not host_is_allowed(lowered.get("host"), self.extra_hosts):
            return error(403, "Use the Pi's address or its .local name.")

        if method in {"GET", "HEAD"}:
            return self._get(path)
        if method not in {"PUT", "POST"}:
            return error(405, "Not allowed")
        if not same_origin(lowered.get("origin"), lowered.get("host")):
            return error(403, "Changes must come from the page itself.")
        if not lowered.get("content-type", "").lower().startswith("application/json"):
            return error(415, "Send JSON.")
        if (len(body) if declared_length is None else declared_length) > MAX_BODY_BYTES:
            return error(413, "Too much data.")
        if declared_length is not None and declared_length < 0:
            return error(400, "Bad request.")

        if method == "PUT" and path == "/api/settings":
            return self._put_settings(body)
        if method == "POST" and path == "/api/silence":
            request_silence(self.run_dir)
            return json_response(200, {"ok": True})
        if method == "POST" and path == "/api/end-cook":
            return self._end_cook()
        return error(404, "Not found")

    def _get(self, path: str) -> Response:
        if path in STATIC:
            name, content_type = STATIC[path]
            try:
                return Response(200, (STATIC_DIR / name).read_bytes(), content_type)
            except OSError:
                return error(500, "The page's files are missing.")
        if path == "/api/settings":
            return self._settings_response(200)
        if path == "/api/status":
            return json_response(200, self._status())
        return error(404, "Not found")

    # ---------------------------------------------------------- settings ---

    def _settings_response(self, status: int) -> Response:
        try:
            current = store.load_settings(self.settings_path, self.info.base_rules)
        except SettingsError as e:
            log.warning("Settings file unusable: %s", e)
            return json_response(
                500,
                {
                    "error": "The settings file is damaged. The monitor is using the last "
                    "good settings. Delete state/settings.json on the Pi to start again.",
                    "damaged": True,
                },
            )
        return json_response(status, self._describe(current))

    def _describe(self, current: Settings) -> dict[str, Any]:
        rules = {f.name: getattr(current.rules, f.name) for f in fields(AlarmRules)}
        recommended = {f.name: getattr(AlarmRules(), f.name) for f in fields(AlarmRules)}
        return {
            "revision": current.revision,
            "units": current.units,
            "rules": rules,
            "recommended": recommended,
            "devices": {i: d.to_json() for i, d in current.devices.items()},
            "brands": {"eti": self.info.has_eti, "traeger": self.info.has_traeger},
            "presets": list(PRESETS),
            "ranges": {
                "minutes": list(store.MINUTES),
                "percent": list(store.PERCENT),
                "offTargetC": list(store.OFF_TARGET_C),
                "probeC": list(store.PROBE_C),
                "pitC": list(store.PIT_C),
                "nameLength": store.NAME_LENGTH,
            },
        }

    def _put_settings(self, body: bytes) -> Response:
        try:
            request = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return error(400, "That wasn't valid JSON.")
        if not isinstance(request, dict):
            return error(400, "Send a JSON object.")
        try:
            saved = store.update_settings(self.settings_path, self.info.base_rules, request)
        except ValidationError as e:
            return json_response(422, {"errors": e.errors})
        except ConflictError as e:
            current = self._settings_response(409)
            return Response(409, _with_error(current.body, str(e)), "application/json")
        except SettingsError:
            return self._settings_response(500)
        log.info("Settings saved from the web page (revision %d)", saved.revision)
        return json_response(200, self._describe(saved))

    # ------------------------------------------------------------ status ---

    def _status(self) -> dict[str, Any]:
        """The monitor's status file with its age, or a note that there isn't one."""
        try:
            data = json.loads((self.run_dir / STATUS_FILE).read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(data["updated_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return {"running": False, "age_seconds": None, "status": None}
        age = max(0.0, (self.clock() - updated).total_seconds())
        return {"running": age <= MONITOR_STALE_SECONDS, "age_seconds": round(age), "status": data}

    def _end_cook(self) -> Response:
        try:
            before = store.load_settings(self.settings_path, self.info.base_rules)
            store.end_cook(self.settings_path, self.info.base_rules)
        except SettingsError:
            return self._settings_response(500)
        request_end_cook(self.run_dir)
        return json_response(200, {"ok": True, "cleared": len(before.custom_limits())})


def _with_error(body: bytes, message: str) -> bytes:
    """A settings reply with an "error" added (for a refused save)."""
    data = json.loads(body)
    data["error"] = message
    return (json.dumps(data) + "\n").encode("utf-8")
