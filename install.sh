#!/usr/bin/env bash
# One-shot installer for Debian/Ubuntu. Run as root from the project root:
#   sudo ./install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Jalankan sebagai root: sudo ./install.sh" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/trustpositif-dashboard

echo "==> Menginstal paket sistem"
apt-get update
apt-get install -y unbound unbound-anchor python3 python3-venv python3-pip python3-unbound python3-lmdb grep curl

echo "==> Menyiapkan DNSSEC root trust anchor"
# The unbound package's own root-auto-trust-anchor-file.conf drop-in
# references /var/lib/unbound/root.key unconditionally - unbound-checkconf
# refuses to validate the config if that file doesn't exist yet. On a
# minimal image (APT recommends disabled), unbound-anchor isn't pulled in
# automatically and nothing ever creates it, so this has to be done
# explicitly rather than assumed. unbound-anchor's own exit code is not a
# reliable success/failure signal (non-zero also covers "already have a
# valid anchor" in some versions) - what matters is that the file exists
# afterward.
mkdir -p /var/lib/unbound
unbound-anchor -a /var/lib/unbound/root.key || true
chown unbound:unbound /var/lib/unbound/root.key

echo "==> Membebaskan port 53 dari systemd-resolved"
# A stock Ubuntu Server image runs systemd-resolved's stub listener on
# 127.0.0.53:53 by default - Unbound's own interface: 0.0.0.0 bind then
# fails at startup with "Address already in use" (confirmed the hard way:
# unbound-checkconf passes fine, but the actual service crash-loops).
# Pointing resolv.conf at 127.0.0.1 (not this box's own public IP) matters
# too - a fresh install's access_control_cidrs is empty, so Unbound's
# compiled-in default only allows 127.0.0.0/8, and this machine's own
# scripts (apt, fetch_trustpositif.py) need to resolve names too before
# anyone's had a chance to add real subscriber CIDR ranges in Pengaturan
# DNS > Kontrol Akses.
if systemctl is-enabled systemd-resolved >/dev/null 2>&1 || systemctl is-active systemd-resolved >/dev/null 2>&1; then
  systemctl disable --now systemd-resolved
fi
rm -f /etc/resolv.conf
printf 'nameserver 127.0.0.1\n' > /etc/resolv.conf

echo "==> Membuat user layanan 'trustpositif'"
id -u trustpositif &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin trustpositif
usermod -aG unbound trustpositif

echo "==> Menyalin aplikasi ke $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$PROJECT_DIR"/app "$PROJECT_DIR"/scripts "$PROJECT_DIR"/unbound "$APP_DIR"/
cp "$PROJECT_DIR"/requirements.txt "$APP_DIR"/

echo "==> Menyiapkan direktori data & config"
mkdir -p /etc/unbound/trustpositif/conf.d /etc/unbound/trustpositif/lmdb
cp -r "$PROJECT_DIR"/pymod /etc/unbound/trustpositif/pymod
chown -R unbound:unbound /etc/unbound/trustpositif
chmod -R 2775 /etc/unbound/trustpositif

echo "==> Menambahkan AppArmor override (pymod perlu menulis top_domains.json)"
# The packaged usr.sbin.unbound profile only grants blanket read access
# under /etc/unbound/ - without this, pymod's periodic top-domains flush
# fails with PermissionError even though Unix file permissions allow it.
APPARMOR_LOCAL=/etc/apparmor.d/local/usr.sbin.unbound
if [ -d /etc/apparmor.d/local ] && ! grep -q "trustpositif: pymod" "$APPARMOR_LOCAL" 2>/dev/null; then
  cat "$PROJECT_DIR/etc/apparmor.d/local/usr.sbin.unbound" >> "$APPARMOR_LOCAL"
  if command -v apparmor_parser >/dev/null 2>&1 && [ -e /etc/apparmor.d/usr.sbin.unbound ]; then
    apparmor_parser -r /etc/apparmor.d/usr.sbin.unbound || true
  fi
fi

echo "==> Membatasi ukuran journal systemd (log_queries bisa sangat besar di resolver sibuk)"
JOURNALD_LOCAL=/etc/systemd/journald.conf.d/trustpositif.conf
mkdir -p /etc/systemd/journald.conf.d
if [ ! -f "$JOURNALD_LOCAL" ]; then
  cp "$PROJECT_DIR/etc/systemd/journald.conf.d/trustpositif.conf" "$JOURNALD_LOCAL"
  systemctl restart systemd-journald
fi

mkdir -p /var/lib/trustpositif/raw
chown -R trustpositif:trustpositif /var/lib/trustpositif

echo "==> Menghubungkan config Unbound"
UBCONF=/etc/unbound/unbound.conf
touch "$UBCONF"
if ! grep -q 'trustpositif/conf.d' "$UBCONF"; then
  echo 'include: "/etc/unbound/trustpositif/conf.d/*.conf"' >> "$UBCONF"
fi
if ! grep -q 'remote-control:' "$UBCONF"; then
  cat >> "$UBCONF" <<'EOF'

remote-control:
    control-enable: yes
    control-interface: 127.0.0.1
    control-use-cert: no
EOF
fi

echo "==> Membuat virtualenv Python & menginstal dependensi"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "==> Membuat .env"
  SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
  read -rsp "Set password admin dashboard: " ADMIN_PW; echo
  HASH=$("$APP_DIR/venv/bin/python" "$APP_DIR/scripts/gen_admin_hash.py" "$ADMIN_PW")
  cat > "$APP_DIR/.env" <<EOF
TP_SECRET_KEY=$SECRET
TP_ADMIN_USER=admin
TP_ADMIN_PASSWORD_HASH=$HASH
TP_DATA_DIR=/var/lib/trustpositif
TP_UNBOUND_DIR=/etc/unbound/trustpositif
TP_UNBOUND_CHECKCONF=sudo /usr/sbin/unbound-checkconf
TP_UNBOUND_RELOAD=sudo /usr/bin/systemctl restart unbound
TP_UNBOUND_CONTROL=sudo /usr/sbin/unbound-control
TP_UNBOUND_CHECKCONF_TIMEOUT=900
TP_UNBOUND_RELOAD_TIMEOUT=900
EOF
fi
chown trustpositif:trustpositif "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "==> Mengunci script sudo-only agar tidak bisa ditulis oleh user 'trustpositif'"
chown root:root "$APP_DIR/scripts/apply_update_schedule.sh"
chmod 755 "$APP_DIR/scripts/apply_update_schedule.sh"

echo "==> Menyiapkan sudoers terbatas untuk user 'trustpositif'"
cat > /etc/sudoers.d/trustpositif <<EOF
trustpositif ALL=(root) NOPASSWD: /usr/sbin/unbound-checkconf, /usr/bin/systemctl reload unbound, /usr/bin/systemctl restart unbound, /usr/sbin/unbound-control, $APP_DIR/scripts/apply_update_schedule.sh, /usr/bin/journalctl -u unbound -u trustpositif-dashboard -u trustpositif-update -n 300 --no-pager
EOF
chmod 440 /etc/sudoers.d/trustpositif
visudo -cf /etc/sudoers.d/trustpositif

echo "==> Seeding database & config awal (agar unbound-checkconf pertama berhasil)"
# db.init_db() below calls Base.metadata.create_all(), which creates every
# table registered on every model - so a fresh install always gets whatever
# the current app/models.py defines with zero extra steps here. A new
# dashboard feature only needs install.sh changes if it also needs a new
# systemd unit, sudoers entry, or pip dependency.
sudo -u trustpositif "$APP_DIR/venv/bin/python" - <<PYEOF
import sys
sys.path.insert(0, "$APP_DIR")
from app import db, unbound_writer, models

db.init_db()
session = db.SessionLocal()
settings = session.query(models.UnboundSettings).first()
unbound_writer.write_conf(settings)
unbound_writer.write_lmdb_blocklist([])
session.close()
PYEOF
chown -R unbound:unbound /etc/unbound/trustpositif

echo "==> Memvalidasi config Unbound"
unbound-checkconf

echo "==> Memasang systemd units"
cp "$APP_DIR/app/trustpositif-dashboard.service" /etc/systemd/system/
cp "$APP_DIR/scripts/trustpositif-update.service" /etc/systemd/system/
cp "$APP_DIR/scripts/trustpositif-update.timer" /etc/systemd/system/
systemctl daemon-reload

echo "==> Mengaktifkan layanan"
systemctl enable --now unbound
systemctl enable --now trustpositif-dashboard.service
systemctl enable --now trustpositif-update.timer

echo "==> Mengambil data TrustPositif untuk pertama kali (bisa beberapa menit, file resmi ~200MB+)"
sudo -u trustpositif "$APP_DIR/venv/bin/python" "$APP_DIR/scripts/fetch_trustpositif.py" || \
  echo "Peringatan: pengambilan awal gagal, cek /var/lib/trustpositif/last_update.json lalu jalankan ulang manual nanti."

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
echo "=================================================================="
echo " Selesai."
echo " Dashboard : http://${IP:-<IP-server-ini>}:8080   (login: admin)"
echo " PENTING   : port 8080 belum di-TLS/reverse-proxy. Batasi lewat"
echo "             firewall/VPN atau taruh di belakang nginx+TLS -"
echo "             lihat docs/SETUP.md bagian Keamanan."
echo "=================================================================="
