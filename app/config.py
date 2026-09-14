import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Explicit path, not the CWD-dependent default: this module gets imported from
# systemd services, install.sh's seeding step, and manual script runs from
# whatever directory the user happens to be in.
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("TP_DATA_DIR", "/var/lib/trustpositif"))
DB_PATH = DATA_DIR / "dashboard.db"
STATUS_FILE = DATA_DIR / "last_update.json"

UNBOUND_DIR = Path(os.getenv("TP_UNBOUND_DIR", "/etc/unbound/trustpositif"))
CONF_D = UNBOUND_DIR / "conf.d"

# LMDB-backed blocklist read by pymod/trustpositif_block.py - memory-mapped,
# so Unbound's resident memory stays small regardless of domain count.
LMDB_DIR = UNBOUND_DIR / "lmdb"
LMDB_BLOCK_PATH = LMDB_DIR / "blocklist"
LMDB_MAP_SIZE_GB = int(os.getenv("TP_LMDB_MAP_SIZE_GB", "8"))

PYMOD_DIR = UNBOUND_DIR / "pymod"
PYMOD_SCRIPT = PYMOD_DIR / "trustpositif_block.py"
PYMOD_ACTION_CONF = PYMOD_DIR / "action.json"

# These are full shell command lines (may include "sudo ...") run via subprocess.
UNBOUND_CHECKCONF = os.getenv("TP_UNBOUND_CHECKCONF", "sudo /usr/sbin/unbound-checkconf")
UNBOUND_RELOAD = os.getenv("TP_UNBOUND_RELOAD", "sudo /usr/bin/systemctl reload unbound")
UNBOUND_CONTROL = os.getenv("TP_UNBOUND_CONTROL", "sudo /usr/sbin/unbound-control")
# /var/log/syslog is root:adm-only (0640) - the trustpositif user isn't in
# adm and shouldn't be added just for this, so the Logs page reads from
# journald instead, merged and pre-scoped to exactly the units this system
# ships (no substring-grepping a whole-system log for "unbound"/"trustpositif").
LOGS_JOURNALCTL = os.getenv(
    "TP_LOGS_JOURNALCTL",
    "sudo /usr/bin/journalctl -u unbound -u trustpositif-dashboard -u trustpositif-update "
    "-n 300 --no-pager",
)
APPLY_UPDATE_SCHEDULE = os.getenv(
    "TP_APPLY_UPDATE_SCHEDULE", "sudo /opt/trustpositif-dashboard/scripts/apply_update_schedule.sh"
)
# Explicit, always-literal commands for the dashboard's manual Restart/Reload
# buttons - deliberately NOT reusing UNBOUND_RELOAD above, since that one is
# overridden in this deployment's .env to actually mean "restart" (see its
# comment); a button labeled "Reload" should always issue a real reload.
UNBOUND_MANUAL_RESTART = "sudo /usr/bin/systemctl restart unbound"
UNBOUND_MANUAL_RELOAD = "sudo /usr/bin/systemctl reload unbound"

# The official TrustPositif RPZ zone can hold tens of millions of records
# (with wildcard subdomains on) - unbound-checkconf/reload parsing that much
# text is genuinely slow on modest hardware, so these need real headroom.
UNBOUND_CHECKCONF_TIMEOUT = int(os.getenv("TP_UNBOUND_CHECKCONF_TIMEOUT", "900"))
UNBOUND_RELOAD_TIMEOUT = int(os.getenv("TP_UNBOUND_RELOAD_TIMEOUT", "900"))

SECRET_KEY = os.getenv("TP_SECRET_KEY", "change-me-please")
ADMIN_USER = os.getenv("TP_ADMIN_USER", "admin")
ADMIN_PASSWORD_HASH = os.getenv("TP_ADMIN_PASSWORD_HASH", "")

# Internal hostname used as the CNAME target when block_mode == "redirect".
# A local-data A record for this name is generated pointing at redirect_ip.
BLOCK_PAGE_DOMAIN = "blocked.trustpositif.local"
