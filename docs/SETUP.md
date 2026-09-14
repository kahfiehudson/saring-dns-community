# Panduan Setup Detail

Untuk penjelasan tiap fitur dashboard (apa itu &amp; cara kerjanya secara teknis), lihat
**docs/FITUR.md**. Dokumen ini fokus ke instalasi, model permission, dan troubleshooting.

## 1. Prasyarat

- **OS**: Ubuntu Server 22.04/24.04 LTS (diverifikasi langsung di tiga server produksi berbeda,
  termasuk instalasi bersih di Ubuntu 24.04 "noble") atau Debian 12 Bookworm (target desain awal
  proyek ini, paket setara tersedia). Juga butuh binding `pythonmod`-nya (`python3-unbound`, lihat
  langkah instalasi) karena blocklist resmi memakai pymod + LMDB, satu-satunya mekanisme blokir di
  proyek ini.
- Akses root (via `sudo`).

### Spesifikasi minimum (berdasarkan pengamatan langsung di produksi)

| | Minimum | Direkomendasikan |
|---|---|---|
| **CPU** | 2 vCPU | 2 vCPU sudah cukup nyaman bahkan di resolver ISP bertrafik tinggi (diamati: jutaan query/hari, ribuan client aktif) |
| **RAM** | 2 GB | 4-8 GB — pymod+LMDB sendiri cuma ~500MB untuk ~9,5 juta domain (lihat README §Arsitektur), tapi RAM ekstra berguna kalau mengaktifkan `log_queries` bersamaan dengan beberapa fitur lain sekaligus |
| **Disk** | 10 GB bebas | 20 GB+ — mencakup OS+paket (~3-4GB), data blocklist (LMDB, di bawah 1GB), unduhan mentah blocklist resmi sementara (~200-250MB), journal systemd yang sudah dibatasi eksplisit (maks. 3GB, lihat docs/FITUR.md §4.1), venv Python (~300MB) |
| **Jaringan** | Port 53 UDP+TCP bebas untuk Unbound; port 8080 untuk dashboard (lihat §6 soal keamanannya) | - |

**Catatan RAM di resolver bertrafik sangat tinggi**: mengaktifkan "Catat semua query DNS"
(`log_queries`, Pengaturan DNS &gt; Logging) di server dengan trafik ISP nyata bisa menaikkan
pemakaian CPU rata-rata secara terukur (pernah diamati naik dari ~15% ke ~27% di server 2 vCPU) karena
volume log yang harus ditulis `systemd-journald` bisa sangat besar (satu client saja bisa
20.000-30.000+ baris log per 5 menit). Ini bukan soal spesifikasi kurang, tapi biaya nyata dari fitur
ini sendiri — lihat docs/FITUR.md §4.1 sebelum mengaktifkannya permanen di server sibuk.

### Dua hal yang sudah ditangani otomatis oleh `install.sh` (dulu perlu perbaikan manual)

`install.sh` sekarang menangani dua masalah umum di instalasi bersih (ditemukan &amp; diperbaiki saat
setup server baru):

1. **DNSSEC root trust anchor belum ada** — di image Ubuntu Server minimal (APT recommends
   dimatikan), paket `unbound-anchor` tidak ikut terpasang otomatis bersama `unbound`, jadi
   `/var/lib/unbound/root.key` tidak pernah dibuat dan `unbound-checkconf` gagal dengan
   `auto-trust-anchor-file: ... does not exist`. `install.sh` sekarang memasang `unbound-anchor` dan
   membuat `root.key` secara eksplisit.
2. **`systemd-resolved` merebut port 53** — Ubuntu Server default menjalankan stub resolver
   `systemd-resolved` di `127.0.0.53:53`, bikin `unbound.service` gagal start dengan "Address already
   in use". `install.sh` sekarang menonaktifkan `systemd-resolved` dan mengarahkan `/etc/resolv.conf`
   ke `127.0.0.1` (bukan IP publik server sendiri — instalasi baru punya Kontrol Akses kosong, jadi
   Unbound cuma allow loopback secara default sampai CIDR subscriber ditambahkan manual, lihat
   docs/FITUR.md §3.3).

Kalau Anda menjalankan `install.sh` versi lama atau di OS/image yang berbeda dan masih menemui salah
satu di atas, jalankan manual:

```bash
# Root trust anchor:
apt-get install -y unbound-anchor
mkdir -p /var/lib/unbound
unbound-anchor -a /var/lib/unbound/root.key || true
chown unbound:unbound /var/lib/unbound/root.key

# systemd-resolved:
systemctl disable --now systemd-resolved
rm -f /etc/resolv.conf
printf 'nameserver 127.0.0.1\n' > /etc/resolv.conf
```

## 2. Instalasi

```bash
git clone <lokasi-proyek-ini>  # atau salin foldernya ke server
cd saring-dns-community
sudo ./install.sh
```

Anda akan diminta membuat password admin dashboard di tengah proses.

Yang dilakukan `install.sh` (urutan sesuai eksekusi):
1. `apt install unbound unbound-anchor python3 python3-venv python3-pip python3-unbound python3-lmdb
   grep curl` (`python3-unbound` = binding pythonmod yang dibutuhkan pymod+LMDB; `python3-lmdb` =
   binding LMDB-nya; `unbound-anchor` dipasang eksplisit, lihat poin 2).
2. **Bootstrap DNSSEC root trust anchor**: `unbound-anchor -a /var/lib/unbound/root.key` +
   kepemilikan `unbound:unbound` — tanpa ini `unbound-checkconf` gagal di image minimal (lihat §1).
3. **Membebaskan port 53**: nonaktifkan `systemd-resolved` kalau aktif, tulis ulang
   `/etc/resolv.conf` ke `nameserver 127.0.0.1` (lihat §1).
4. Membuat user sistem `trustpositif` (tanpa login shell), dijadikan anggota group `unbound`.
5. Menyalin `app/`, `scripts/`, `unbound/` ke `/opt/trustpositif-dashboard`.
6. Membuat `/etc/unbound/trustpositif/{conf.d,lmdb}` + menyalin `pymod/` ke
   `/etc/unbound/trustpositif/pymod`, dimiliki `unbound:unbound`, mode `2775` (setgid) — supaya user
   `trustpositif` (anggota group `unbound`) bisa menulis file baru di sana, sementara daemon Unbound
   (user `unbound`) tetap bisa membacanya.
7. Menambahkan override AppArmor (pymod perlu menulis `top_domains.json`, profil bawaan
   `usr.sbin.unbound` tidak mengizinkannya secara default).
8. Memasang drop-in `journald.conf.d/trustpositif.conf` (`SystemMaxUse=3G`, `MaxRetentionSec=2day`) —
   supaya `log_queries` (kalau nanti diaktifkan) tidak menggerus jatah disk tanpa batas, lihat
   docs/FITUR.md §4.1.
9. Menambahkan baris `include: ".../conf.d/*.conf"` dan blok `remote-control:` ke
   `/etc/unbound/unbound.conf` yang sudah ada (tidak menimpa file yang sudah ada, hanya menambah
   kalau belum ada).
10. Membuat virtualenv Python & install dependency (`requirements.txt` — FastAPI, SQLAlchemy, `lmdb`,
    dst.).
11. Membuat `/opt/trustpositif-dashboard/.env` berisi secret key dan hash password admin (bcrypt) —
    dilewati kalau `.env` sudah ada (idempotent, aman dijalankan ulang).
12. Mengunci script sudo-only (`chown root:root`, `chmod 755`) supaya tidak bisa ditulis oleh
    user `trustpositif` sendiri.
13. Memasang `/etc/sudoers.d/trustpositif` — **hanya** mengizinkan perintah spesifik tanpa password:
    `unbound-checkconf`, `systemctl reload/restart unbound`, `unbound-control`, dan
    `apply_update_schedule.sh` — tidak ada akses root lain.
14. Seed database SQLite + config awal (LMDB blocklist kosong) supaya validasi config pertama kali
    tidak gagal karena file belum ada.
15. Validasi `unbound-checkconf`.
16. Memasang &amp; mengaktifkan systemd units: `trustpositif-dashboard.service`,
    `trustpositif-update.timer`.
17. Menjalankan pengambilan data TrustPositif untuk pertama kali (~200MB+, bisa beberapa menit).

Catatan: langkah 5 (menyalin `app/`) dan seeding database di langkah 14 (`db.init_db()`, yang
memanggil `Base.metadata.create_all()`) bersifat generik - keduanya otomatis mengikuti apa pun yang
ada di `app/models.py` saat ini.

## 3. Verifikasi

```bash
systemctl status unbound
systemctl status trustpositif-dashboard
systemctl status trustpositif-update.timer
journalctl -u trustpositif-dashboard -f

# Uji langsung ke resolver:
dig @127.0.0.1 <domain-yang-diketahui-diblokir> +short
dig @127.0.0.1 subdomain-acak.<domain-yang-diblokir> +short   # harus ikut NXDOMAIN juga
```

Buka `http://<ip-server>:8080`, login dengan `admin` + password yang tadi
diset. Akun ini otomatis tersimpan ke tabel user dashboard saat pertama kali
jalan - untuk ganti password, pakai halaman **Akun Saya** di dashboard
(tidak perlu edit `.env` lagi setelah ini). Community Edition hanya
mendukung satu akun login (tanpa role/multi-user).

## 4. Model permission (penting jika Anda debug manual)

- Unbound daemon berjalan sebagai user **`unbound`**.
- Dashboard & update timer berjalan sebagai user **`trustpositif`**, anggota
  group **`unbound`**.
- `/etc/unbound/trustpositif/{conf.d,lmdb}` dimiliki `unbound:unbound`,
  mode `2775` → `trustpositif` bisa menulis file baru di situ (lewat
  keanggotaan group), Unbound bisa membaca file yang ditulis
  `trustpositif` (lewat permission group juga).
- `trustpositif` **tidak** punya akses root secara umum — hanya perintah di
  `/etc/sudoers.d/trustpositif` yang bisa dijalankan tanpa password.

## 5. Update berkala

`trustpositif-update.timer` berjalan tiap hari jam 04:00 (+ jitter acak 30
menit) lewat `trustpositif-update.service`. Ubah jadwal dengan mengedit
`OnCalendar=` di `/etc/systemd/system/trustpositif-update.timer`, lalu:

```bash
sudo systemctl daemon-reload
sudo systemctl restart trustpositif-update.timer
```

Catatan: update memicu `restart` Unbound (bukan `reload`) — restart penuh
justru lebih ringan di sini karena Unbound tidak perlu menyimpan data
lama+baru sekaligus di memori seperti saat `reload`. Restart hanya makan
waktu <1 detik karena LMDB dibuka lewat memory-map, tidak perlu dimuat penuh
ke RAM saat startup.

## 5b. Akses ke sumber resmi

Endpoint resmi Komdigi (`trustpositif.komdigi.go.id/assets/db/domains_isp`)
tampaknya memfilter berdasarkan IP sumber di level firewall mereka, bukan
berdasarkan negara — sebuah VPS di Indonesia pun bisa tetap kena *timeout*
kalau IP/ASN-nya tidak dikenali (indikasinya: koneksi mati persis di titik
peering menuju jaringan mereka, bukan gagal duluan di jaringan Anda). Ini
konsisten dengan cara kerja historis Trust Positif yang menghendaki akses
database mentah hanya untuk infrastruktur ISP yang terdaftar resmi.

Kalau ini terjadi ke Anda, `scripts/fetch_trustpositif.py` akan gagal dan
mencatat error-nya di status update (lihat kartu "Status Update Terakhir")
— tidak ada fallback ke sumber lain di luar Komdigi; blocklist yang sudah
ada tetap dipakai sampai update berikutnya berhasil.

**Untuk akses resmi jangka panjang**: hubungi APJII (apjii.or.id) — asosiasi
ISP Indonesia yang berkoordinasi dengan Komdigi soal Trust Positif. Tidak ada
formulir pendaftaran akses database publik yang terdokumentasi; ini perlu
ditanyakan langsung sebagai bagian dari proses keanggotaan/kerja sama
penyelenggara jaringan.

## 6. Keamanan dashboard

Dashboard (`uvicorn`, port 8080) **tidak** memakai TLS secara default dan
memakai session cookie sederhana. Untuk penggunaan produksi, pilih salah
satu (atau keduanya):

- **Cepat, tanpa domain**: batasi lewat firewall
  (`ufw allow from <IP-kantor> to any port 8080`) atau taruh di belakang VPN.
  Jangan expose `0.0.0.0:8080` langsung ke internet publik.

- **Direkomendasikan, dengan domain**: jalankan `scripts/setup_nginx.sh`
  setelah `install.sh`. Prasyarat: domain (mis. `dns-admin.contoh.id`) sudah
  di-DNS-kan ke IP publik server ini.

  ```bash
  sudo ./scripts/setup_nginx.sh dns-admin.contoh.id admin@contoh.id
  ```

  Script ini otomatis: install nginx + certbot, membuat virtual host,
  menerbitkan & memasang sertifikat Let's Encrypt lewat
  `certbot --nginx` (termasuk redirect HTTP→HTTPS), lalu mengubah
  `ExecStart` di `trustpositif-dashboard.service` supaya `uvicorn` hanya
  bind ke `127.0.0.1:8080` — port 8080 tidak lagi bisa diakses langsung dari
  luar server. Aman dijalankan ulang kapan saja. Setelah ini, akses dashboard
  lewat `https://dns-admin.contoh.id`, bukan lagi `http://<ip>:8080`.

  Kalau memakai `ufw`, buka `Nginx Full` dan tutup akses langsung ke 8080:
  ```bash
  sudo ufw allow 'Nginx Full'
  sudo ufw delete allow 8080/tcp
  ```

  `nginx/trustpositif-dashboard.conf` di root proyek adalah salinan referensi
  konfigurasi yang dibuat script ini — tidak perlu disalin manual, cukup
  untuk dibaca.

## 7. Troubleshooting

- **`unbound-checkconf` gagal setelah menyimpan Settings** — pesan errornya
  ditampilkan langsung di halaman Settings; config lama tetap dipakai karena
  restart tidak pernah dipanggil kalau `checkconf` gagal.
- **Statistik di dashboard kosong** — cek `remote-control` aktif di
  `unbound.conf` dan `trustpositif` bisa jalankan `sudo unbound-control` tanpa
  password (`sudo -u trustpositif sudo unbound-control stats_noreset`).
- **Domain yang seharusnya diblokir masih bisa diakses** — kemungkinan
  klien memakai resolver DNS lain (cek pengaturan jaringan klien), atau
  browser memakai DNS-over-HTTPS bawaan (lihat bagian Keterbatasan di
  README).
- **Restart lambat / memori Unbound besar** — cek `free -h` setelah restart;
  seharusnya pymod+LMDB tetap di kisaran beberapa ratus MB terlepas dari
  ukuran daftar domain. Kalau jauh lebih besar dari itu, cek apakah
  `10-features.conf` masih memuat `module-config` dengan `python` di
  dalamnya (lihat poin berikutnya) — tanpa itu, pymod tidak aktif sama sekali
  dan Unbound diam-diam tidak memblokir apa pun.
- **`module conf '...' is not known to work` saat checkconf** —
  `unbound-checkconf` hanya menerima kombinasi `module-config` yang sudah
  dikenal secara eksplisit di source code-nya (lihat `unbound-checkconf.c`).
  Kombinasi yang benar di sini adalah `"python validator iterator"` (python
  harus di depan) — ini sudah ditangani otomatis oleh `unbound_writer.py`,
  tapi kalau meng-edit config manual, gunakan urutan ini persis.
- **`unbound.service` gagal start dengan `Address already in use` untuk port 53** — `systemd-resolved`
  masih aktif dan merebut port itu duluan. `install.sh` versi terbaru sudah menanganinya otomatis
  (lihat §1); kalau masih terjadi, jalankan manual: `systemctl disable --now systemd-resolved`, lalu
  tulis ulang `/etc/resolv.conf` ke `nameserver 127.0.0.1`.
- **`unbound-checkconf` gagal dengan `auto-trust-anchor-file: ... does not exist`** — paket
  `unbound-anchor` belum terpasang (umum di image minimal dengan APT recommends dimatikan) dan
  `/var/lib/unbound/root.key` belum pernah dibuat. `install.sh` versi terbaru menanganinya otomatis
  (lihat §1); perbaikan manual ada di kotak kode §1.
- **Script sendiri (`fetch_trustpositif.py`, `apt`, dst.) gagal resolve nama domain, padahal internet
  jalan** (`Temporary failure in name resolution` atau `dig` balik `REFUSED`/`recursion not
  available`) — biasanya karena `/etc/resolv.conf` mengarah ke **IP publik** server ini sendiri
  (bukan `127.0.0.1`), sementara Kontrol Akses (Pengaturan DNS) masih kosong. Instalasi baru yang
  Kontrol Akses-nya belum diisi CIDR apa pun membuat Unbound cuma allow `127.0.0.0/8` secara default
  (lihat docs/FITUR.md §3.3) — query dari IP publik sendiri pun ikut REFUSED. `install.sh` sudah
  mengarahkan `resolv.conf` ke `127.0.0.1` supaya ini tidak terjadi; kalau menemukan resolv.conf sudah
  diubah manual ke IP publik, kembalikan ke `127.0.0.1` atau tambahkan IP publik server ini sendiri ke
  Kontrol Akses.
