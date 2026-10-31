"""The real web server, on a spare port, with a real HTTP client."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from smoker_monitor.domain.alarms import AlarmRules
from smoker_monitor.web.api import WebApp, WebInfo
from smoker_monitor.web.server import make_server


@pytest.fixture
def port(tmp_path: Path) -> Iterator[int]:
    server = make_server(WebApp(tmp_path, WebInfo(AlarmRules())), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield int(server.server_address[1])
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def request(
    port: int,
    method: str,
    path: str,
    body: object = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    sent = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=data, headers=sent)
    response = connection.getresponse()
    payload = response.read()
    result = (response.status, {k.lower(): v for k, v in response.getheaders()}, payload)
    connection.close()
    return result


def test_serves_the_page_and_the_settings(port: int) -> None:
    status, headers, body = request(port, "GET", "/")
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert b"<title>Smoker</title>" in body
    status, _, body = request(port, "GET", "/api/settings")
    assert status == 200
    assert json.loads(body)["revision"] == 0


def test_a_save_round_trips(port: int) -> None:
    status, _, body = request(port, "PUT", "/api/settings", {"revision": 0, "units": "F"})
    assert status == 200
    assert json.loads(body)["units"] == "F"
    _, _, again = request(port, "GET", "/api/settings")
    assert json.loads(again)["units"] == "F"


def test_head_has_no_body(port: int) -> None:
    status, headers, body = request(port, "HEAD", "/")
    assert status == 200
    assert body == b""
    assert int(headers["content-length"]) > 0


def test_a_made_up_host_name_is_refused(port: int) -> None:
    status, _, _ = request(port, "GET", "/api/settings", headers={"Host": "evil.example.com"})
    assert status == 403


def test_a_huge_upload_is_refused_without_reading_it_all(port: int) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest("PUT", "/api/settings")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(10_000_000))
    connection.endheaders()
    connection.send(b" " * 1000)
    response = connection.getresponse()
    assert response.status == 413
    connection.close()


def test_a_nonsense_length_is_survived(port: int) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest("PUT", "/api/settings")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", "lots")
    connection.endheaders()
    response = connection.getresponse()
    assert response.status in {400, 413}
    connection.close()
    assert request(port, "GET", "/api/settings")[0] == 200  # still serving
