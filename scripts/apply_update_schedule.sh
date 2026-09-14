#!/usr/bin/env bash
# Invoked by the dashboard (via sudo, see app/unbound_writer.py apply_update_schedule())
# to change how often trustpositif-update.timer fires. Re-validates the value
# against the same whitelist the dashboard itself enforces - defense in depth,
# since this runs as root via a NOPASSWD sudoers entry.
set -euo pipefail

VALUE="${1:-}"

case "$VALUE" in
  "*-*-* *:00:00"|"*-*-* 0/3:00:00"|"*-*-* 0/6:00:00"|"*-*-* 0/12:00:00"|"*-*-* 04:00:00")
    ;;
  *)
    echo "apply_update_schedule.sh: nilai jadwal tidak dikenal: '$VALUE'" >&2
    exit 1
    ;;
esac

mkdir -p /etc/systemd/system/trustpositif-update.timer.d
cat > /etc/systemd/system/trustpositif-update.timer.d/override.conf <<EOF
[Timer]
OnCalendar=
OnCalendar=$VALUE
EOF

systemctl daemon-reload
systemctl restart trustpositif-update.timer
