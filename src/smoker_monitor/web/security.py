"""Who may use the page.

There is no PIN (the page is for your home network), so the protection is in
where requests may come from. The server refuses anything that is not:

1. From a home-network address. Loopback (this Pi), the private ranges
   (192.168.x.x, 10.x.x.x, 172.16-31.x.x), link-local, and Tailscale's 100.64.0.0/10. A
   request arriving from the public internet (say, a router forwarding a port
   by mistake) gets "403 Forbidden".
2. Addressed to a home name. Browsers can be tricked into talking to your Pi
   from a malicious web page by "DNS rebinding" (a made-up web address that
   suddenly points at 192.168.x.x). Those requests carry the made-up name in
   their Host header, so only IP addresses, "localhost", single-word names
   ("raspberrypi"), and ".local" / ".lan" / ".home.arpa" / ".ts.net" names are
   accepted. More can be added with `--allow-host`.
3. For changes (PUT/POST): sent as JSON, and from this page. A web page on some
   other site can't send JSON to another address without the server's
   permission, and any "Origin" header must match the page's own address.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit

HOME_SUFFIXES = (".local", ".lan", ".home.arpa", ".ts.net")

# Exactly these and nothing else (Python's own "is private" also covers some
# ranges that aren't home networks).
HOME_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",  # this Pi
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",  # the usual home ranges
        "169.254.0.0/16",  # link-local
        "100.64.0.0/10",  # Tailscale
        "::1/128",
        "fc00::/7",  # a home IPv6 network
        "fe80::/10",  # IPv6 link-local
    )
)


def client_is_local(address: str) -> bool:
    """True if `address` (the other end of the connection) is on a home network."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])  # drop an IPv6 zone like %eth0
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in HOME_NETWORKS)


def hostname_of(host_header: str | None) -> str | None:
    """The name or address part of a Host header ("pi.local:8080" -> "pi.local")."""
    if not host_header:
        return None
    try:
        return urlsplit("//" + host_header).hostname
    except ValueError:
        return None


def host_is_allowed(host_header: str | None, extra: Iterable[str] = ()) -> bool:
    """True if the request was addressed to a home name or an IP address."""
    name = hostname_of(host_header)
    if name is None:
        return False
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        return True  # an IP address can't be a rebinding trick
    if name in {h.lower() for h in extra} or name == "localhost":
        return True
    return "." not in name or name.endswith(HOME_SUFFIXES)


def same_origin(origin: str | None, host_header: str | None) -> bool:
    """True if there is no Origin header, or it names this very server."""
    if origin is None:
        return True  # not a browser cross-site request (those always say where from)
    try:
        sent = urlsplit(origin).netloc
    except ValueError:
        return False
    return sent.lower() == (host_header or "").lower()
