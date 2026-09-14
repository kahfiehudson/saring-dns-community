#!/usr/bin/env python3
"""Download the Komdigi TrustPositif domain list and rebuild the LMDB
blocklist database used by pymod/trustpositif_block.py. Meant to be run by
the trustpositif-update.timer unit (or manually), as the `trustpositif`
system user.

The official endpoint appears to firewall off a lot of non-whitelisted
source IPs - including plenty of legitimate Indonesian VPS/hosting
providers, not just traffic from outside Indonesia (confirmed via mtr: the
connection dies right at the destination's peering point, not upstream).
Only this official source is used - if it can't be reached, the update
simply fails (see status/message in the dashboard) rather than silently
substituting an unofficial data source.
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, unbound_writer  # noqa: E402
from app.validate import normalize_domain  # noqa: E402

OFFICIAL_URL = "https://trustpositif.komdigi.go.id/assets/db/domains_isp"


def download(url: str, dest: Path, timeout: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "trustpositif-unbound-updater/1.0"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)


def fetch_raw_domains_file(data_dir: Path) -> Path:
    raw_dir = data_dir / "raw"
    official_path = raw_dir / "domains_isp.txt"
    download(OFFICIAL_URL, official_path, timeout=600)
    return official_path


def parse_domains(path: Path):
    seen = set()
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            d = normalize_domain(line)
            if d and d not in seen:
                seen.add(d)
                yield d


def main() -> None:
    started = datetime.now(timezone.utc)
    status = {"started_at": started.isoformat()}

    try:
        raw_path = fetch_raw_domains_file(config.DATA_DIR)
        status["downloaded_bytes"] = raw_path.stat().st_size

        domains = parse_domains(raw_path)
        # No wildcard entries needed - trustpositif_block.py checks every
        # parent suffix of the query name against the same database.
        total = unbound_writer.write_lmdb_blocklist(domains)
        status["backend"] = "pymod_lmdb"

        ok, msg = unbound_writer.check_and_reload()
        status.update(
            {
                "status": "ok" if ok else "reload_failed",
                "total_domains": total,
                "message": msg,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if not ok:
            print(f"WARNING: reload failed: {msg}", file=sys.stderr)
    except Exception as e:
        status.update(
            {
                "status": "error",
                "message": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"ERROR: {e}", file=sys.stderr)
        raise
    finally:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.STATUS_FILE.write_text(json.dumps(status, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
