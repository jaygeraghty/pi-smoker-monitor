"""Who may use the settings page: home network only (web/security.py)."""

from __future__ import annotations

import pytest

from smoker_monitor.web.security import (
    client_is_local,
    host_is_allowed,
    hostname_of,
    same_origin,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "::1",
        "192.168.1.20",
        "10.0.0.7",
        "172.16.5.5",
        "169.254.10.10",  # link-local
        "100.100.1.1",  # Tailscale
        "fd00::1",  # a home IPv6 network
        "fe80::1%eth0",  # IPv6 link-local with its zone
        "::ffff:192.168.1.20",  # a home address as an IPv6-mapped one
    ],
)
def test_home_addresses_are_allowed(address: str) -> None:
    assert client_is_local(address)


@pytest.mark.parametrize(
    "address",
    ["8.8.8.8", "203.0.113.9", "100.128.0.1", "2001:db8::1", "::ffff:8.8.8.8", "", "nonsense"],
)
def test_internet_addresses_are_refused(address: str) -> None:
    assert not client_is_local(address)


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.50:8080",
        "[::1]:8080",
        "localhost:8080",
        "raspberrypi:8080",
        "pi.local",
        "PI.LOCAL:8080",
        "pi.lan",
        "router.home.arpa",
        "pi.tail1234.ts.net",
    ],
)
def test_home_names_are_allowed(host: str) -> None:
    assert host_is_allowed(host)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "evil.example.com",
        "evil.example.com:8080",
        "192.168.1.1.evil.com",
        "pi.local.evil.com",
    ],
)
def test_other_names_are_refused_to_stop_dns_rebinding(host: str | None) -> None:
    assert not host_is_allowed(host)


def test_extra_names_can_be_allowed() -> None:
    assert host_is_allowed("bbq.example.com:8080", extra=["BBQ.example.com"])


def test_hostname_of() -> None:
    assert hostname_of("pi.local:8080") == "pi.local"
    assert hostname_of("[::1]:8080") == "::1"
    assert hostname_of(None) is None


def test_origin_must_match_the_page() -> None:
    assert same_origin(None, "pi.local:8080")  # not a cross-site browser request
    assert same_origin("http://pi.local:8080", "pi.local:8080")
    assert not same_origin("http://evil.example.com", "pi.local:8080")
    assert not same_origin("http://pi.local:9999", "pi.local:8080")
    assert not same_origin("null", "pi.local:8080")
