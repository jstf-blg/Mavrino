"""url_safety.py — SSRF guard for fetching remote (image) URLs.

The pipeline sideloads product images from URLs that come from partly-untrusted
sources (Apify/SerpApi scrapes, the seed catalogue, and — via search-to-generate
— demand the public can influence). Before fetching any such URL on a CI runner,
validate it points at a real *public* host, not an internal service or a cloud
metadata endpoint (169.254.169.254, 127.0.0.1, 10.0.0.0/8, ::1, …).

Usage:
    from url_safety import is_safe_public_url
    if not is_safe_public_url(url):
        return None  # refuse to fetch
"""

import ipaddress
import socket
from urllib.parse import urlparse


def _ip_is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def is_safe_public_url(url: str) -> bool:
    """True only if url is http(s) and every IP its host resolves to is public.

    Resolving and checking *all* A/AAAA records (not just one) closes the gap
    where a hostname returns both a public and a private address.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = p.hostname
    if not host:
        return False
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(_ip_is_public(a) for a in addrs)
