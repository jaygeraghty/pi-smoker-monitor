"""Talking to Traeger's cloud: log in, find your grills, and listen to them.

Traeger has no public API. This follows what the app does (as worked out by
the open-source Home Assistant Traeger integrations):

1. Log in with your Traeger email and password (Amazon Cognito), giving a
   token that lasts about an hour.
2. Ask for your grills: GET /users/self lists them as "things", each with a
   "thingName" (its id) and "friendlyName" (the name you gave it).
3. Ask for a signed MQTT address (POST /mqtt-connections), valid for a while
   ("expirationSeconds"), and connect to it over a secure websocket.
4. Subscribe to prod/thing/update/<thingName>. While the grill is on and
   online, it sends its status there every few seconds.
5. Ask each grill to send its status straight away (command "90"), so we don't
   wait for the next update.

READ-ONLY, ALWAYS. This app must never change anything on your grill. So:
- The MQTT connection only ever subscribes; nothing is ever published.
- The only command that can be sent is "90" ("send me your status"), and
  `send_command` refuses anything not in ALLOWED_COMMANDS. Never add to it.

The password is never logged or shown in any message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import aiomqtt

from smoker_monitor.config import TraegerSettings
from smoker_monitor.sources.base import SourceError

log = logging.getLogger(__name__)

COGNITO_URL = "https://cognito-idp.us-west-2.amazonaws.com/"
API_URL = "https://mobile-iot-api.iot.traegergrills.io"
UPDATE_TOPIC = "prod/thing/update/"

# "Send me your status". The ONLY command this app may ever send: anything
# else (set temperature, shut down, ...) would control the grill.
STATUS_REQUEST = "90"
ALLOWED_COMMANDS = frozenset({STATUS_REQUEST})

# Renew the login token and the MQTT address this long before they expire.
RENEW_EARLY_SECONDS = 60


@dataclass(frozen=True)
class GrillMessage:
    """One status message from a grill, as received."""

    thing: str  # the grill's thingName
    payload: dict[str, Any]  # the message, decoded from JSON
    received_at: float  # time.time() when it arrived


class TraegerCloud:
    """A logged-in connection to Traeger's cloud. Use with `async with`."""

    def __init__(self, settings: TraegerSettings, timeout_seconds: float = 20) -> None:
        self._settings = settings
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires = 0.0

    async def __aenter__(self) -> TraegerCloud:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        session, self._session = self._session, None
        self._token = None
        if session is not None:
            await session.close()

    # ------------------------------------------------------------- REST ---

    async def grills(self) -> list[dict[str, Any]]:
        """Your grills ("things"), each with at least a thingName."""
        user = await self._request("GET", "/users/self")
        things = user.get("things")
        if not isinstance(things, list):
            raise SourceError("Traeger returned no grills for this login")
        return [thing for thing in things if isinstance(thing, dict) and thing.get("thingName")]

    async def mqtt_address(self) -> tuple[str, float]:
        """A signed MQTT websocket address, and when it expires (time.time())."""
        asked_at = time.time()
        reply = await self._request("POST", "/mqtt-connections")
        url, seconds = reply.get("signedUrl"), reply.get("expirationSeconds")
        if not isinstance(url, str) or not isinstance(seconds, int | float):
            raise SourceError("Traeger didn't return an MQTT address")
        return url, asked_at + seconds

    async def send_command(self, thing: str, command: str) -> None:
        """Send a command to a grill. Only ALLOWED_COMMANDS ("90", status) may be sent."""
        if command not in ALLOWED_COMMANDS:
            # A programming error, not a cloud problem: refuse loudly.
            raise PermissionError(f"Refusing to send command {command!r}: this app is read-only")
        await self._request("POST", f"/things/{thing}/commands", {"command": command})

    async def request_status(self, thing: str) -> None:
        """Ask a grill to send its status now."""
        await self.send_command(thing, STATUS_REQUEST)

    # ------------------------------------------------------------- MQTT ---

    async def listen(
        self,
        things: list[str],
        on_message: Callable[[GrillMessage], None],
        seconds: float | None = None,
    ) -> None:
        """Pass every status message from these grills to `on_message`.

        Returns when the MQTT address is about to expire (call it again for a
        fresh one), or after `seconds` if given. Subscribes only; never
        publishes. Raises SourceError if the connection fails.
        """
        url, expires = await self.mqtt_address()
        stop_in = expires - RENEW_EARLY_SECONDS - time.time()
        if seconds is not None:
            stop_in = min(stop_in, seconds)
        parts = urlsplit(url)
        path = f"{parts.path}?{parts.query}" if parts.query else parts.path
        try:
            async with (
                asyncio.timeout(max(stop_in, 1)),
                aiomqtt.Client(
                    parts.netloc,
                    port=443,
                    transport="websockets",
                    websocket_path=path,
                    websocket_headers={"Host": parts.netloc},
                    tls_context=ssl.create_default_context(),
                    keepalive=120,
                ) as client,
            ):
                for thing in things:
                    await client.subscribe(f"{UPDATE_TOPIC}{thing}", qos=1)
                for thing in things:
                    await self.request_status(thing)
                async for message in client.messages:
                    grill_message = decode(message.topic.value, message.payload)
                    if grill_message is not None:
                        on_message(grill_message)
        except TimeoutError:
            return  # time's up: the normal way out
        except aiomqtt.MqttError as e:
            raise SourceError(f"Lost the connection to Traeger ({e})") from e

    # ---------------------------------------------------------- helpers ---

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call Traeger's API, logged in, and return the JSON reply."""
        token = await self._log_in()
        try:
            async with self._http().request(
                method, API_URL + path, json=body, headers={"Authorization": token}
            ) as response:
                if response.status >= 400:
                    raise SourceError(f"Traeger said no to {path} (HTTP {response.status})")
                text = await response.text()
        except aiohttp.ClientError as e:
            raise SourceError(f"Couldn't reach Traeger ({type(e).__name__})") from e
        if not text:
            return {}
        try:
            reply = json.loads(text)
        except json.JSONDecodeError:
            raise SourceError(f"Traeger sent something unexpected for {path}") from None
        return reply if isinstance(reply, dict) else {}

    async def _log_in(self) -> str:
        """A valid login token, logging in again if it's about to expire."""
        if self._token is not None and time.time() < self._token_expires - RENEW_EARLY_SECONDS:
            return self._token
        asked_at = time.time()
        body = {
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": self._settings.client_id,
            "AuthParameters": {
                "USERNAME": self._settings.email,
                "PASSWORD": self._settings.password,
            },
            "ClientMetadata": {},
        }
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }
        try:
            async with self._http().post(COGNITO_URL, json=body, headers=headers) as response:
                reply = await response.json(content_type=None)
        except (aiohttp.ClientError, json.JSONDecodeError) as e:
            raise SourceError(f"Couldn't reach Traeger to log in ({type(e).__name__})") from e

        result = reply.get("AuthenticationResult") if isinstance(reply, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("IdToken"), str):
            # Never include the reply: keep the message short and password-free.
            kind = reply.get("__type", "") if isinstance(reply, dict) else ""
            if "NotAuthorized" in str(kind):
                raise SourceError("Traeger login failed: check [traeger_mqtt] email and password")
            raise SourceError("Traeger login failed")
        self._token = result["IdToken"]
        self._token_expires = asked_at + float(result.get("ExpiresIn", 3600))
        return self._token

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session


def decode(topic: str, payload: object) -> GrillMessage | None:
    """A status message from an MQTT message, or None if it isn't one."""
    if not topic.startswith(UPDATE_TOPIC):
        return None
    if isinstance(payload, bytes | bytearray):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        log.warning("Ignoring a Traeger message that isn't JSON")
        return None
    if not isinstance(data, dict):
        return None
    return GrillMessage(thing=topic[len(UPDATE_TOPIC) :], payload=data, received_at=time.time())
