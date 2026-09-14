from __future__ import annotations

import ipaddress
import re

_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN_RE = re.compile(rf"^({_LABEL}\.)+[a-z]{{2,63}}$", re.IGNORECASE)


def normalize_domain(raw: str) -> str | None:
    """Clean up one raw entry from the TrustPositif dump (or a dashboard form)
    into a bare, lowercase, validated domain name - or None if it can't be used.

    Handles: surrounding whitespace, comment lines, a leading scheme
    (http://, https://), a trailing path/query, a trailing port, and legacy
    "ip<TAB>domain" style rows (keeps the last whitespace-separated token).
    """
    s = raw.strip().lower()
    if not s or s.startswith("#"):
        return None

    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/")[0]

    if any(c.isspace() for c in s):
        s = s.split()[-1]

    s = s.split(":")[0]
    s = s.strip(".")

    if not s or len(s) > 253:
        return None

    if not s.isascii():
        try:
            s = s.encode("idna").decode("ascii")
        except Exception:
            return None

    if not DOMAIN_RE.match(s):
        return None

    return s


def normalize_resolver(raw: str) -> str | None:
    """Validate one upstream resolver entry for the Domain Forwarder feature:
    a bare IPv4/IPv6 address, or that address with an "@port" suffix (the
    same syntax Unbound's forward-addr accepts, e.g. "9.9.9.9@853" for
    DNS-over-TLS) - or None if it can't be used."""
    s = raw.strip()
    if not s:
        return None

    port = None
    ip_part = s
    if "@" in s:
        ip_part, _, port_part = s.rpartition("@")
        if not port_part.isdigit():
            return None
        port = int(port_part)
        if not (0 < port <= 65535):
            return None

    try:
        ip_str = str(ipaddress.ip_address(ip_part))
    except ValueError:
        return None

    return f"{ip_str}@{port}" if port else ip_str


def normalize_ip(raw: str) -> str | None:
    """Validate one Local DNS Records entry: a bare IPv4/IPv6 address, no
    port (a static host record answers a plain A/AAAA, unlike the
    resolver-address syntax normalize_resolver() handles) - or None if it
    can't be used."""
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None
