"""What each address of the settings page does (web/api.py), without a network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from smoker_monitor.domain.alarms import AlarmRules
from smoker_monitor.service.monitor import END_COOK_FILE, SILENCE_FILE
from smoker_monitor.service.settings import SETTINGS_FILE
from smoker_monitor.service.status import STATUS_FILE
from smoker_monitor.web.api import (
    MAX_BODY_BYTES,
    MONITOR_STALE_SECONDS,
    STATIC,
    STATIC_DIR,
    Response,
    WebApp,
    WebInfo,
)

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def make_app(tmp_path: Path, **info: Any) -> WebApp:
    return WebApp(tmp_path, WebInfo(AlarmRules(), **info), clock=lambda: NOW)


def call(
    app: WebApp,
    method: str,
    path: str,
    body: Any = None,
    *,
    client: str = "192.168.1.20",
    host: str = "pi.local:8080",
    content_type: str | None = "application/json",
    origin: str | None = None,
    raw: bytes | None = None,
) -> Response:
    headers = {"Host": host}
    if content_type:
        headers["Content-Type"] = content_type
    if origin:
        headers["Origin"] = origin
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    return app.handle(method, path, headers, data, client)


def as_json(response: Response) -> Any:
    return json.loads(response.body)


def get_settings(app: WebApp) -> Any:
    return as_json(call(app, "GET", "/api/settings"))


# ------------------------------------------------------------ the page ---


def test_the_page_and_its_files_are_served(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for path, (name, content_type) in STATIC.items():
        response = call(app, "GET", path)
        assert response.status == 200, path
        assert response.body == (STATIC_DIR / name).read_bytes()
        assert response.content_type == content_type


def test_the_page_names_every_file_it_loads() -> None:
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for path in ("/app.css", "/app.js", "/favicon.svg", "/manifest.webmanifest"):
        assert path in page and path in STATIC


def test_the_page_has_no_inline_script_or_style(tmp_path: Path) -> None:
    """The security policy forbids them, so they would silently not work."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "<style" not in page
    assert " style=" not in page
    assert "onclick=" not in page
    assert "<script>" not in page


def test_every_reply_carries_the_security_headers(tmp_path: Path) -> None:
    response = call(make_app(tmp_path), "GET", "/")
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert call(make_app(tmp_path), "GET", "/api/status").headers["Cache-Control"] == "no-store"
    assert call(make_app(tmp_path), "GET", "/nothing", client="8.8.8.8").headers[
        "Content-Security-Policy"
    ]


def test_only_listed_files_are_served(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for path in ("/config.toml", "/../config.toml", "/static/app.js", "/api", "/%2e%2e/etc/passwd"):
        assert call(app, "GET", path).status == 404, path


def test_a_query_string_is_ignored(tmp_path: Path) -> None:
    assert call(make_app(tmp_path), "GET", "/app.js?v=3").status == 200


# ------------------------------------------------------------ who may ask ---


@pytest.mark.parametrize("client", ["8.8.8.8", "203.0.113.5", "garbage"])
def test_requests_from_the_internet_are_refused(tmp_path: Path, client: str) -> None:
    app = make_app(tmp_path)
    assert call(app, "GET", "/", client=client).status == 403
    assert call(app, "GET", "/api/settings", client=client).status == 403
    assert call(app, "POST", "/api/silence", {}, client=client).status == 403
    assert not (tmp_path / SILENCE_FILE).exists()


def test_a_made_up_web_address_is_refused(tmp_path: Path) -> None:
    response = call(make_app(tmp_path), "GET", "/api/settings", host="evil.example.com")
    assert response.status == 403


def test_changes_from_another_website_are_refused(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    response = call(app, "POST", "/api/silence", {}, origin="http://evil.example.com")
    assert response.status == 403
    assert not (tmp_path / SILENCE_FILE).exists()


def test_changes_from_the_page_itself_are_accepted(tmp_path: Path) -> None:
    response = call(make_app(tmp_path), "POST", "/api/silence", {}, origin="http://pi.local:8080")
    assert response.status == 200


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_changes_must_be_sent_as_json(tmp_path: Path, content_type: str | None) -> None:
    """A form on another site can send these types without asking permission; JSON it can't."""
    app = make_app(tmp_path)
    assert call(app, "POST", "/api/silence", {}, content_type=content_type).status == 415
    assert (
        call(app, "PUT", "/api/settings", {"revision": 0}, content_type=content_type).status == 415
    )
    assert not (tmp_path / SILENCE_FILE).exists()


def test_other_methods_are_not_allowed(tmp_path: Path) -> None:
    assert call(make_app(tmp_path), "DELETE", "/api/settings").status == 405


def test_too_much_data_is_refused(tmp_path: Path) -> None:
    big = b" " * (MAX_BODY_BYTES + 1)
    assert call(make_app(tmp_path), "PUT", "/api/settings", raw=big).status == 413


# -------------------------------------------------------------- settings ---


def test_settings_describe_themselves(tmp_path: Path) -> None:
    data = get_settings(make_app(tmp_path, has_traeger=True))
    assert data["revision"] == 0
    assert data["units"] == "C"
    assert data["rules"]["pit_high"] is True
    assert data["recommended"] == data["rules"]
    assert data["brands"] == {"eti": True, "traeger": True}
    assert data["ranges"]["minutes"] == [1, 60]
    assert {p["name"] for p in data["presets"]} == {"Brisket", "Pork shoulder", "Chicken"}
    assert data["devices"] == {}


def test_the_config_file_rules_show_as_the_current_ones(tmp_path: Path) -> None:
    app = WebApp(tmp_path, WebInfo(AlarmRules(probe_low=True)))
    data = get_settings(app)
    assert data["rules"]["probe_low"] is True
    assert data["recommended"]["probe_low"] is False  # recommended is the program's own


def test_saving_changes_the_settings_and_the_revision(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    body = {
        "revision": 0,
        "alarms": {"pit_low": False, "probe_timeout_minutes": 10},
        "units": "F",
        "devices": {"eti:P1": {"name": "Brisket", "high": {"mode": "set", "c": 93}}},
    }
    response = call(app, "PUT", "/api/settings", body)
    assert response.status == 200
    data = as_json(response)
    assert data["revision"] == 1
    assert data["rules"]["pit_low"] is False
    assert data["rules"]["probe_timeout_minutes"] == 10
    assert data["units"] == "F"
    assert data["devices"]["eti:P1"] == {"name": "Brisket", "high": {"mode": "set", "c": 93.0}}
    assert get_settings(app) == data  # and it was kept


def test_a_second_phone_with_old_settings_is_told_not_overwritten(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    call(app, "PUT", "/api/settings", {"revision": 0, "units": "F"})
    stale = call(app, "PUT", "/api/settings", {"revision": 0, "units": "C"})
    assert stale.status == 409
    data = as_json(stale)
    assert "Someone else" in data["error"]
    assert data["units"] == "F"  # the latest, so the page can offer to reload it
    assert get_settings(app)["units"] == "F"


def test_silly_values_get_a_message_per_field(tmp_path: Path) -> None:
    response = call(
        make_app(tmp_path),
        "PUT",
        "/api/settings",
        {"revision": 0, "alarms": {"probe_timeout_minutes": 500}, "units": "K"},
    )
    assert response.status == 422
    assert set(as_json(response)["errors"]) == {"alarms.probe_timeout_minutes", "units"}
    assert not (tmp_path / SETTINGS_FILE).exists()


@pytest.mark.parametrize("raw", [b"not json", b"\xff\xfe", b"[]", b'"text"', b"7"])
def test_rubbish_is_a_400(tmp_path: Path, raw: bytes) -> None:
    assert call(make_app(tmp_path), "PUT", "/api/settings", raw=raw).status == 400


def test_a_damaged_settings_file_is_explained_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / SETTINGS_FILE).write_text("{ broken", encoding="utf-8")
    app = make_app(tmp_path)
    for response in (
        call(app, "GET", "/api/settings"),
        call(app, "PUT", "/api/settings", {"revision": 0}),
        call(app, "POST", "/api/end-cook", {}),
    ):
        assert response.status == 500
        assert as_json(response)["damaged"] is True
        assert "settings.json" in as_json(response)["error"]


def test_an_unknown_address_is_a_404(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert call(app, "PUT", "/api/other", {}).status == 404
    assert call(app, "POST", "/api/settings", {}).status == 404


def test_a_bug_becomes_a_plain_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path)

    def boom() -> dict[str, Any]:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(app, "_status", boom)
    response = call(app, "GET", "/api/status")
    assert response.status == 500
    assert "secret detail" not in response.body.decode()


# ---------------------------------------------------------------- status ---


def write_status(tmp_path: Path, age: timedelta) -> None:
    status = {"schema": 2, "updated_at": (NOW - age).isoformat(), "pits": [], "probes": []}
    (tmp_path / STATUS_FILE).write_text(json.dumps(status), encoding="utf-8")


def test_status_without_a_monitor(tmp_path: Path) -> None:
    data = as_json(call(make_app(tmp_path), "GET", "/api/status"))
    assert data == {"running": False, "age_seconds": None, "status": None}


def test_status_says_how_old_it_is(tmp_path: Path) -> None:
    write_status(tmp_path, timedelta(seconds=7))
    data = as_json(call(make_app(tmp_path), "GET", "/api/status"))
    assert data["running"] is True
    assert data["age_seconds"] == 7
    assert data["status"]["schema"] == 2


def test_an_old_status_means_the_monitor_has_stopped(tmp_path: Path) -> None:
    write_status(tmp_path, timedelta(seconds=MONITOR_STALE_SECONDS + 1))
    assert as_json(call(make_app(tmp_path), "GET", "/api/status"))["running"] is False


@pytest.mark.parametrize("text", ["{ broken", "[]", '{"no": "time"}', '{"updated_at": "never"}'])
def test_an_unreadable_status_is_treated_as_no_monitor(tmp_path: Path, text: str) -> None:
    (tmp_path / STATUS_FILE).write_text(text, encoding="utf-8")
    assert as_json(call(make_app(tmp_path), "GET", "/api/status"))["running"] is False


# --------------------------------------------------- silence and end cook ---


def test_silence_leaves_a_request_for_the_monitor(tmp_path: Path) -> None:
    assert call(make_app(tmp_path), "POST", "/api/silence", {}).status == 200
    assert (tmp_path / SILENCE_FILE).exists()


def test_end_cook_clears_own_limits_and_tells_the_monitor(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    call(
        app,
        "PUT",
        "/api/settings",
        {
            "revision": 0,
            "alarms": {"pit_low": False},
            "devices": {
                "eti:P1": {"name": "Brisket", "high": {"mode": "set", "c": 93}},
                "eti:P2": {"low": {"mode": "off"}},
            },
        },
    )
    response = call(app, "POST", "/api/end-cook", {})
    assert as_json(response) == {"ok": True, "cleared": 2}
    assert (tmp_path / END_COOK_FILE).exists()
    after = get_settings(app)
    assert after["devices"] == {"eti:P1": {"name": "Brisket"}}
    assert after["rules"]["pit_low"] is False
