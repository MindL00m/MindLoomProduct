"""Guards for server-side requests to URLs that users or models choose.

``check_public_url`` refuses anything that is not plain HTTP(S) to a public
address: loopback, private networks (10/8, 172.16/12, 192.168/16, fc00::/7),
link-local and cloud metadata (169.254.0.0/16), carrier-grade NAT, multicast and
reserved ranges. Every address the host name resolves to is checked, and callers
must not follow redirects (a redirect could point back inside the network).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

_BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata"}


class UnsafeUrl(ValueError):
    """The URL points somewhere the server must not call."""


def _address_is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or ip in ipaddress.ip_network("100.64.0.0/10")
    )


async def check_public_url(url: str) -> str:
    """Return the URL when it is an http(s) URL whose host resolves only to public
    addresses; raise :class:`UnsafeUrl` otherwise."""

    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrl("Only http and https URLs are allowed.")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith((".internal", ".local", ".localhost")):
        raise UnsafeUrl("That host is not allowed.")
    if parts.username or parts.password:
        raise UnsafeUrl("URLs with credentials are not allowed.")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _address_is_public(str(literal)):
            raise UnsafeUrl("Private, local and metadata addresses are not allowed.")
        return url
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, parts.port or (443 if parts.scheme == "https" else 80)
        )
    except socket.gaierror as exc:
        raise UnsafeUrl("That host name does not resolve.") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_address_is_public(a) for a in addresses):
        raise UnsafeUrl("Private, local and metadata addresses are not allowed.")
    return url
