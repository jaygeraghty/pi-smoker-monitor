"""Tests for talking to Traeger's cloud, without the network.

The most important ones check the app stays read-only: the only command it
can ever send is "90" (send me your status).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from smoker_monitor.config import TraegerSettings
from smoker_monitor.sources import traeger_cloud
from smoker_monitor.sources.base import SourceError
from smoker_monitor.sources.traeger_cloud import ALLOWED_COMMANDS, TraegerCloud, decode

SETTINGS = TraegerSettings(email="grill@example.com", password="grill-pw", client_id="client")


class RecordingCloud(TraegerCloud):
    """A TraegerCloud whose API calls are recorded instead of sent."""

    def __init__(self) -> None:
        super().__init__(SETTINGS)
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        return {}


# ------------------------------------------------------------- read-only ---


def test_status_request_is_the_only_allowed_command() -> None:
    assert frozenset({"90"}) == ALLOWED_COMMANDS


async def test_status_request_is_sent() -> None:
    cloud = RecordingCloud()
    await cloud.request_status("GRILL-1")
    assert cloud.calls == [("POST", "/things/GRILL-1/commands", {"command": "90"})]


@pytest.mark.parametrize("command", ["11,225", "17", "14,74", "12,600", "20", "90,", " 90"])
async def test_any_other_command_is_refused_before_sending(command: str) -> None:
    """Set temperature, shut down, probe target, timer, super smoke...: never."""
    cloud = RecordingCloud()
    with pytest.raises(PermissionError, match="read-only"):
        await cloud.send_command("GRILL-1", command)
    assert cloud.calls == []


# ---------------------------------------------------------------- login ---


class FakeResponse:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self.reply


class FakeSession:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.sent: list[Any] = []

    def post(self, url: str, json: Any, headers: dict[str, str]) -> FakeResponse:
        self.sent.append(json)
        return FakeResponse(self.reply)


def cloud_answering(reply: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> TraegerCloud:
    cloud = TraegerCloud(SETTINGS)
    session = FakeSession(reply)
    monkeypatch.setattr(cloud, "_http", lambda: session)
    return cloud


async def test_login_keeps_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"AuthenticationResult": {"IdToken": "tok", "ExpiresIn": 3600}}
    cloud = cloud_answering(reply, monkeypatch)
    assert await cloud._log_in() == "tok"
    assert await cloud._log_in() == "tok"  # reused, not logged in twice


async def test_wrong_password_says_so_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = {"__type": "NotAuthorizedException", "message": "Incorrect username or password."}
    cloud = cloud_answering(reply, monkeypatch)
    with pytest.raises(SourceError, match="email and password") as caught:
        await cloud._log_in()
    assert "grill-pw" not in str(caught.value)


async def test_odd_login_reply_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = cloud_answering({"ChallengeName": "NEW_PASSWORD_REQUIRED"}, monkeypatch)
    with pytest.raises(SourceError, match="Traeger login failed"):
        await cloud._log_in()


# --------------------------------------------------------------- decode ---


def test_decode_status_message() -> None:
    payload = json.dumps({"status": {"grill": 225}}).encode()
    message = decode("prod/thing/update/GRILL-1", payload)
    assert message is not None
    assert message.thing == "GRILL-1"
    assert message.payload == {"status": {"grill": 225}}


@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        ("prod/thing/other/GRILL-1", b"{}"),  # not a status topic
        ("prod/thing/update/GRILL-1", b"not json"),
        ("prod/thing/update/GRILL-1", b"[1, 2]"),  # JSON, but not an object
        ("prod/thing/update/GRILL-1", None),
    ],
)
def test_decode_ignores_anything_else(topic: str, payload: object) -> None:
    assert decode(topic, payload) is None


def test_listening_never_publishes() -> None:
    """Belt and braces: nothing in the module calls publish()."""
    source = Path(traeger_cloud.__file__).read_text(encoding="utf-8")
    assert ".publish(" not in source
