from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

import lmdb

from . import config


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _atomic_lmdb_swap(final_path: Path, map_size_bytes: int, put_pairs) -> int:
    """Build an LMDB env into a temp sibling directory, then swap it into
    place with a rename - readers that reopen the env afterward (pymod's
    init_standard runs once at Unbound startup, so this pairs with a
    systemctl restart, not a hot reload) see a complete, consistent database,
    never a half-written one."""
    tmp_dir = final_path.with_name(final_path.name + ".tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    env = lmdb.open(str(tmp_dir), map_size=map_size_bytes, max_dbs=0, subdir=True)
    count = 0
    BATCH_SIZE = 200_000
    batch: list[str] = []

    def flush(txn):
        for d in batch:
            txn.put(d.encode("ascii", "ignore"), b"1")
        batch.clear()

    with env.begin(write=True) as txn:
        for d in put_pairs:
            batch.append(d)
            count += 1
            if len(batch) >= BATCH_SIZE:
                flush(txn)
        flush(txn)
    env.sync()
    env.close()

    if final_path.exists():
        shutil.rmtree(final_path)
    tmp_dir.rename(final_path)
    return count


def write_lmdb_blocklist(domains: Iterable[str]) -> int:
    """Rebuild the big official-blocklist LMDB database used by
    pymod/trustpositif_block.py."""
    seen: set[str] = set()

    def unique():
        for d in domains:
            if d not in seen:
                seen.add(d)
                yield d

    return _atomic_lmdb_swap(config.LMDB_BLOCK_PATH, config.LMDB_MAP_SIZE_GB * 1024**3, unique())


# SafeSearch enforcement: forces each provider's own official safe-mode
# endpoint via Unbound's native local-zone/local-data - no pymod involved,
# since this is a small, fixed set of well-known domains (unlike the
# millions-of-domains blocklist that drove the pymod+LMDB design). A CNAME
# under local-zone type "redirect" is a documented Unbound feature meant
# exactly for this: Unbound resolves the CNAME target itself (a real,
# publicly-resolvable domain the provider controls) and returns the full
# chain in one answer, rather than just handing the client a bare CNAME.
SAFESEARCH_CNAME_PROVIDERS = {
    "google": {
        "label": "Google Search",
        "domains": ["google.com", "www.google.com", "google.co.id", "www.google.co.id"],
        "cname": "forcesafesearch.google.com.",
    },
    "youtube": {
        "label": "YouTube (Mode Terbatas - Strict)",
        "domains": [
            "www.youtube.com", "m.youtube.com", "youtubei.googleapis.com",
            "youtube.googleapis.com", "www.youtube-nocookie.com",
        ],
        # Verified against a live resolver: restrict.youtube.com is the real
        # strict-mode endpoint - restrictmoderate.youtube.com is the
        # (weaker) moderate mode, and a "restrictstrict" variant does not
        # actually exist (confirmed via a real NXDOMAIN from Google's own
        # authoritative servers when it was tried).
        "cname": "restrict.youtube.com.",
    },
    "duckduckgo": {
        "label": "DuckDuckGo",
        "domains": ["duckduckgo.com", "www.duckduckgo.com"],
        "cname": "safe.duckduckgo.com.",
    },
    "bing": {
        "label": "Bing",
        "domains": ["bing.com", "www.bing.com"],
        # Bing's strict-SafeSearch endpoint used to be a fixed IP, but that's
        # stale now - Microsoft moved it behind Azure Front Door, so (like
        # every other provider here) it's a CNAME with a dynamically-resolved
        # target. Verified against a live resolver: strict.bing.com -> ...
        # ax-msedge.net, not the old hardcoded 204.79.197.220 some DNS
        # filtering guides still cite.
        "cname": "strict.bing.com.",
    },
}
# (setting_field, provider_dict) - drives both write_safesearch_conf() and
# the Settings page checkboxes, so adding a provider only means editing this
# list plus one new UnboundSettings column.
SAFESEARCH_TOGGLES = [
    ("safesearch_google", SAFESEARCH_CNAME_PROVIDERS["google"]),
    ("safesearch_youtube", SAFESEARCH_CNAME_PROVIDERS["youtube"]),
    ("safesearch_bing", SAFESEARCH_CNAME_PROVIDERS["bing"]),
    ("safesearch_duckduckgo", SAFESEARCH_CNAME_PROVIDERS["duckduckgo"]),
]


def write_safesearch_conf(settings) -> None:
    # local-zone:/local-data: are server: sub-directives - each conf.d file
    # is spliced in as its own independent set of top-level clauses (not
    # merged into 10-features.conf's server: block), so this needs its own
    # server: header too, same as every other generated conf.d file here.
    lines: list[str] = []
    for field, provider in SAFESEARCH_TOGGLES:
        if not getattr(settings, field, False):
            continue
        for domain in provider["domains"]:
            lines.append(f'    local-zone: "{domain}." redirect')
            lines.append(f'    local-data: "{domain}. 300 IN CNAME {provider["cname"]}"')
    content = ("server:\n" + "\n".join(lines) + "\n") if lines else ""
    _atomic_write(config.CONF_D / "40-safesearch.conf", content)


def parse_cidr_entries(raw: str) -> list[str]:
    """Split the Kontrol Akses field into individual "cidr action" entries -
    one per line (the preferred format, easiest to add/remove a single
    entry without touching the rest) or comma-separated (still accepted so
    older saved data, or a single-line paste, keeps working). Malformed
    entries (wrong token count) are silently dropped, same lenient
    behavior this had before line-based input was supported."""
    entries = []
    for item in re.split(r"[,\n]+", raw):
        item = item.strip()
        if not item:
            continue
        parts = item.split()
        if len(parts) != 2:
            continue
        entries.append(f"{parts[0]} {parts[1]}")
    return entries


def _access_control_lines(raw: str) -> list[str]:
    return [f"    access-control: {entry}" for entry in parse_cidr_entries(raw)]


def write_conf(settings) -> None:
    """Regenerate the small include files under conf.d/."""
    features = ["server:"]
    # Bind to every interface (not just the loopback Unbound falls back to
    # when no interface: is set at all) - otherwise the server's own public
    # IP refuses connections outright (nothing listening there), which is
    # what a client pointed at that IP - including this box's own
    # /etc/resolv.conf, if set that way - would hit.
    features.append("    interface: 0.0.0.0")
    features.append("    interface: ::0")
    features.append(f"    qname-minimisation: {'yes' if settings.qname_minimisation else 'no'}")
    # pymod is mandatory - it's the only mechanism for the official
    # blocklist (see write_lmdb_blocklist). Unbound's checkconf only accepts
    # a fixed set of known-good module-config strings (verified against
    # unbound-checkconf.c source for 1.19.2) - "python" must come first when
    # present.
    module_config = "python validator iterator" if settings.dnssec_enabled else "python iterator"
    features.append(f'    module-config: "{module_config}"')
    features.append(f"    verbosity: {int(settings.verbosity)}")
    features.append(f"    log-queries: {'yes' if settings.log_queries else 'no'}")

    features.extend(_access_control_lines(settings.access_control_cidrs))

    if settings.block_mode == "redirect" and settings.redirect_ip:
        features.append(f'    local-data: "{config.BLOCK_PAGE_DOMAIN}. A {settings.redirect_ip}"')
        features.append(f'    local-data-ptr: "{settings.redirect_ip} {config.BLOCK_PAGE_DOMAIN}"')

    if settings.resolve_mode == "forward":
        fwd = ['forward-zone:', '    name: "."']
        if settings.forward_use_tls:
            fwd.append("    forward-tls-upstream: yes")
        for up in settings.forward_upstreams.split(","):
            up = up.strip()
            if up:
                fwd.append(f"    forward-addr: {up}")
        features.append("\n".join(fwd))

    features.append("python:")
    features.append(f'    python-script: "{config.PYMOD_SCRIPT}"')
    # pymod has no access to the dashboard's SQLite settings - it reads this
    # tiny JSON file once at init_standard() instead, so block_mode actually
    # takes effect for the LMDB-backed blocklist too.
    _atomic_write(
        config.PYMOD_ACTION_CONF,
        json.dumps({"action": settings.block_mode, "redirect_ip": settings.redirect_ip}),
    )

    _atomic_write(config.CONF_D / "10-features.conf", "\n".join(features) + "\n")

    write_safesearch_conf(settings)


# (label, OnCalendar= value) - fixed set only. apply_update_schedule() and
# scripts/apply_update_schedule.sh both re-check against this, since the
# script runs as root via a NOPASSWD sudoers entry and must never accept
# free-form text.
UPDATE_SCHEDULE_CHOICES = [
    ("Setiap 1 jam", "*-*-* *:00:00"),
    ("Setiap 3 jam", "*-*-* 0/3:00:00"),
    ("Setiap 6 jam", "*-*-* 0/6:00:00"),
    ("Setiap 12 jam", "*-*-* 0/12:00:00"),
    ("Setiap 24 jam (jam 04:00 WIB)", "*-*-* 04:00:00"),
]
_VALID_SCHEDULES = {value for _, value in UPDATE_SCHEDULE_CHOICES}


def apply_update_schedule(value: str) -> tuple[bool, str]:
    if value not in _VALID_SCHEDULES:
        return False, f"Jadwal tidak dikenal: {value!r}"
    try:
        result = subprocess.run(
            shlex.split(config.APPLY_UPDATE_SCHEDULE) + [value],
            capture_output=True, text=True, timeout=30,
        )
        ok = result.returncode == 0
        return ok, (result.stdout + result.stderr).strip() or "Jadwal update diterapkan"
    except Exception as e:
        return False, str(e)


def next_update_run() -> str:
    """Best-effort - returns '' if systemctl/timer isn't queryable (e.g. no
    sudo rights configured yet)."""
    try:
        result = subprocess.run(
            ["systemctl", "list-timers", "trustpositif-update.timer", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "trustpositif-update.timer" in line and "NEXT" not in line:
                # First whitespace-split columns are the NEXT date/time, e.g.
                # "Wed 2026-09-02 04:06:50 WIB".
                parts = line.split()
                if len(parts) >= 4:
                    return " ".join(parts[:4])
    except Exception:
        pass
    return ""


def check_conf() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            shlex.split(config.UNBOUND_CHECKCONF),
            capture_output=True, text=True, timeout=config.UNBOUND_CHECKCONF_TIMEOUT,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, (
            f"unbound-checkconf belum selesai setelah {config.UNBOUND_CHECKCONF_TIMEOUT}s - "
            "coba naikkan TP_UNBOUND_CHECKCONF_TIMEOUT di .env."
        )
    except Exception as e:
        return False, str(e)


def reload_unbound() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            shlex.split(config.UNBOUND_RELOAD),
            capture_output=True, text=True, timeout=config.UNBOUND_RELOAD_TIMEOUT,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, (
            f"reload unbound belum selesai setelah {config.UNBOUND_RELOAD_TIMEOUT}s - "
            "coba naikkan TP_UNBOUND_RELOAD_TIMEOUT di .env, atau cek "
            "`systemctl status unbound` langsung di server (reload mungkin masih "
            "berjalan di background)."
        )
    except Exception as e:
        return False, str(e)


def check_and_reload() -> tuple[bool, str]:
    ok, msg = check_conf()
    if not ok:
        return False, f"unbound-checkconf gagal:\n{msg}"
    ok, msg = reload_unbound()
    if not ok:
        return False, f"reload unbound gagal:\n{msg}"
    return True, "Konfigurasi diterapkan"


def apply(settings) -> tuple[bool, str]:
    write_conf(settings)
    return check_and_reload()


def unbound_service_status() -> dict:
    """Read-only systemctl queries - no sudo needed, unlike restart/reload/
    unbound-control (systemd exposes unit state to any local user by
    default)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "unbound"], capture_output=True, text=True, timeout=10,
        )
        state = result.stdout.strip() or "unknown"
    except Exception:
        state = "unknown"

    since = ""
    try:
        result = subprocess.run(
            ["systemctl", "show", "unbound", "--property=ActiveEnterTimestamp", "--value"],
            capture_output=True, text=True, timeout=10,
        )
        since = result.stdout.strip()
    except Exception:
        pass

    return {"active": state == "active", "state": state, "since": since}


def manual_restart_unbound() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            shlex.split(config.UNBOUND_MANUAL_RESTART), capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip() or "Unbound berhasil di-restart"
    except Exception as e:
        return False, str(e)


def manual_reload_unbound() -> tuple[bool, str]:
    ok, msg = check_conf()
    if not ok:
        return False, f"unbound-checkconf gagal, reload dibatalkan:\n{msg}"
    try:
        result = subprocess.run(
            shlex.split(config.UNBOUND_MANUAL_RELOAD), capture_output=True, text=True, timeout=60,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip() or "Unbound berhasil di-reload"
    except Exception as e:
        return False, str(e)


def read_status() -> dict:
    if config.STATUS_FILE.exists():
        try:
            return json.loads(config.STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def query_unbound_stats() -> dict:
    try:
        result = subprocess.run(
            shlex.split(config.UNBOUND_CONTROL) + ["stats_noreset"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {}
        stats = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                stats[k] = v
        return stats
    except Exception:
        return {}
