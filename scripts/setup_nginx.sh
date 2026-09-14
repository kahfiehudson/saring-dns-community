#!/usr/bin/env bash
# Optional hardening step - run this AFTER install.sh, once DNS for your
# chosen domain already points at this server's public IP:
#
#   sudo ./scripts/setup_nginx.sh dns-admin.contoh.id admin@contoh.id
#
# Puts the dashboard behind nginx + Let's Encrypt TLS, then rebinds uvicorn
# to 127.0.0.1 so port 8080 is no longer reachable directly from outside.
# Safe to re-run.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Jalankan sebagai root: sudo ./scripts/setup_nginx.sh <domain> <email>" >&2
  exit 1
fi

DOMAIN="${1:-}"
EMAIL="${2:-}"
if [ -z "$DOMAIN" ]; then
  read -rp "Domain untuk dashboard (mis. dns-admin.contoh.id): " DOMAIN
fi
if [ -z "$EMAIL" ]; then
  read -rp "Email untuk Let's Encrypt (notifikasi kedaluwarsa sertifikat): " EMAIL
fi

SITE_CONF=/etc/nginx/sites-available/trustpositif-dashboard
UNIT=/etc/systemd/system/trustpositif-dashboard.service

echo "==> Menginstal nginx & certbot"
apt-get update
apt-get install -y nginx certbot python3-certbot-nginx

echo "==> Menulis config nginx untuk $DOMAIN"
cat > "$SITE_CONF" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf "$SITE_CONF" /etc/nginx/sites-enabled/trustpositif-dashboard
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Meminta sertifikat TLS dari Let's Encrypt (butuh DNS $DOMAIN sudah mengarah ke server ini)"
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos -n --redirect

if [ -f "$UNIT" ]; then
  echo "==> Membatasi dashboard agar hanya bisa diakses lewat nginx (127.0.0.1)"
  sed -i 's/--host 0\.0\.0\.0/--host 127.0.0.1/' "$UNIT"
  systemctl daemon-reload
  systemctl restart trustpositif-dashboard
else
  echo "Peringatan: $UNIT tidak ditemukan - jalankan install.sh dulu." >&2
fi

echo ""
echo "=================================================================="
echo " Selesai. Dashboard sekarang di: https://$DOMAIN"
echo " Port 8080 tidak lagi bisa diakses langsung dari luar server ini."
echo " Jika pakai ufw, pastikan:"
echo "   ufw allow 'Nginx Full'"
echo "   ufw delete allow 8080/tcp   # kalau sebelumnya sempat dibuka"
echo "=================================================================="
