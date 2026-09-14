# Dokumentasi Fitur SARING DNS Community

Referensi teknis lengkap: setiap fitur, cara kerjanya, dan dependensinya. Untuk instalasi lihat
**docs/SETUP.md**; untuk ringkasan/arsitektur lihat **README.md**.

Beberapa istilah yang dipakai berulang di dokumen ini:

- **"Butuh `log_queries`"** — fitur itu hanya punya data kalau "Catat semua query DNS" (Pengaturan
  DNS &gt; Logging) aktif. Unbound baru menulis satu baris log per query ke journald kalau opsi ini
  nyala — di resolver dengan trafik tinggi ini bisa jadi volume log yang sangat besar.

---

## 1. Blocklist & Filtering

### 1.1 Blocklist Resmi TrustPositif

**Apa itu**: daftar domain resmi dari Komdigi yang wajib diblokir ISP di Indonesia.

**Metode**: `scripts/fetch_trustpositif.py` men-download `trustpositif.komdigi.go.id/assets/db/domains_isp`
(satu domain per baris, bisa 200MB+), lalu membangun database **LMDB** (`pymod/trustpositif_block.py`)
yang di-*memory-map* oleh Unbound (bukan dimuat penuh ke RAM). Blokir subdomain otomatis lewat
*parent-suffix matching* saat query (`a.b.example.com` dicek sebagai `a.b.example.com`, `b.example.com`,
`example.com`) — tidak perlu entry wildcard terpisah per domain.

Kenapa LMDB, bukan RPZ (mekanisme blokir bawaan Unbound): RPZ butuh ~5,9GB RAM untuk ~9,5 juta domain
tanpa wildcard, atau OOM di 16GB+ dengan wildcard (RPZ butuh 2 record/domain dan Unbound memuat
seluruh zona ke memori). LMDB+pymod terukur **~500MB RAM** untuk jumlah domain yang sama, restart
Unbound &lt;1 detik.

**Sumber tunggal**: hanya endpoint resmi Komdigi yang dipakai (lihat docs/SETUP.md §5b soal
pemfilteran IP sumber di sisi mereka) — kalau tidak terjangkau, update gagal dan errornya dicatat di
`last_update.json` serta ditampilkan di kartu "Status Update Terakhir" (Dashboard); blocklist yang
sudah ada tetap dipakai sampai update berikutnya berhasil.

**Jadwal**: `trustpositif-update.timer`, diatur di Pengaturan DNS &gt; Jadwal Sinkronisasi Blocklist
(1/3/6/12/24 jam, default 24 jam jam 04:00 WIB + jitter 30 menit).

### 1.2 Mode Blokir (NXDOMAIN / Redirect)

**Metode**: dibaca pymod dari `action.json` (ditulis `unbound_writer.py`, karena pymod tidak punya
akses ke database SQLite dashboard) — sekali per apply config. **NXDOMAIN**: domain terlihat tidak
terdaftar sama sekali. **Redirect**: jawaban A record langsung ke IP halaman blokir (bukan CNAME ke
nama perantara) — sengaja menghindari masalah *resolution-chain* yang muncul kalau redirect lewat
CNAME.

---

## 2. Resolusi

### 2.1 Mode Resolusi (Recursive / Forward)

**Recursive**: Unbound resolve langsung dari root server sendiri, standar. **Forward**: semua query
diteruskan ke upstream tertentu (`forward-zone: name: "."`) — dipakai kalau ISP ingin pakai resolver
upstream (mis. 1.1.1.1, 8.8.8.8) alih-alih resolusi rekursif penuh.

**Gunakan DNS-over-TLS ke upstream** (`forward_use_tls`): mengaktifkan `forward-tls-upstream: yes` —
**hanya berlaku kalau Mode Resolusi = forward**. Kalau Mode Resolusi = recursive, seluruh blok
`forward-zone:` (termasuk directive TLS ini) tidak pernah ditulis ke config sama sekali — checkbox
ini tersimpan di database tapi nganggur, tidak berefek apa pun ke resolusi.

---

## 3. Keamanan

### 3.1 DNSSEC, QNAME Minimisation

Toggle standar Unbound (`harden-*`, `qname-minimisation`) — tidak ada mekanisme custom, langsung
dipetakan ke directive Unbound yang sama.

### 3.2 SafeSearch Enforcement

**Metode**: `local-zone: redirect` + CNAME ke endpoint resmi tiap provider (Google, YouTube, Bing,
DuckDuckGo) — Unbound sendiri yang meneruskan resolusi CNAME itu, bukan jawaban statis. Blocklist
tetap menang kalau domain yang sama juga ada di daftar blokir.

### 3.3 Kontrol Akses

**Kontrol Akses**: `access-control:` per baris CIDR (`allow`/`refuse`/`deny`). **Penting**: kalau
kosong sama sekali, default bawaan Unbound cuma allow `127.0.0.0/8` + `::1` — semua sumber lain
otomatis REFUSED. Ini yang bikin instalasi baru (server fresh) sempat gagal resolve dari mesin itu
sendiri sebelum CIDR subscriber ditambahkan (lihat docs/SETUP.md).

---

## 4. Logging & Monitoring

### 4.1 Catat Semua Query DNS (`log_queries`)

**Konsekuensi nyata**: satu baris log per query ke journald (`info: <client-ip> <domain>. <TYPE>
<CLASS>`). Di resolver ISP dengan trafik tinggi, ini **bisa sangat besar**. Journal systemd dibatasi
eksplisit (`SystemMaxUse=3G`, `MaxRetentionSec=2day`, drop-in
`etc/systemd/journald.conf.d/trustpositif.conf`, dipasang otomatis oleh install.sh) supaya volume ini
tidak menggerus jatah disk service lain tanpa batas.

**Dampak CPU/RAM nyata**: menyalakan ini di resolver bertrafik tinggi bisa menaikkan pemakaian CPU
cukup signifikan (proses `systemd-journald` sendiri ikut menulis log). Pertimbangkan mematikannya lagi
kalau tidak sedang aktif dibutuhkan.

### 4.2 Query Real-time, Server Real-time, Cache Hit-Rate

**Metode**: kartu Dashboard yang menampilkan angka live (cache hit-rate, KPI query, CPU/RAM/Disk)
polling tiap ~3 detik lewat `fetch()` JS biasa ke `/api/live-stats` &amp; `/api/live-resources` — bukan
WebSocket, sengaja: datanya murah dihitung (baca `unbound-control` + `/proc`), jadi polling sudah
cukup ringan. Semua angka ini adalah snapshot sejak Unbound terakhir start/restart, bukan riwayat
historis jangka panjang.

### 4.3 Kontrol Unbound

Status Aktif/Tidak Aktif live (systemd), tombol restart/reload manual.

---

## 5. Dashboard & Akun

### 5.1 Login Admin Tunggal

Satu akun login (dibuat via `install.sh`/`scripts/gen_admin_hash.py`), tanpa role/multi-user — siapa
pun yang bisa login punya akses penuh ke semua halaman. Halaman "Akun Saya" hanya untuk ganti password
sendiri (butuh password saat ini untuk konfirmasi).

### 5.2 Activity Log & Changelog

**Activity Log**: 20 aksi terakhir lewat dashboard (bukan log sistem mentah) — simpan pengaturan,
tambah/hapus domain, restart/reload manual, dst. **Changelog**: `app/version.py` adalah satu-satunya
sumber kebenaran versi &amp; riwayat fitur (footer, halaman `/changelog`, dan `CHANGELOG.md` harus
selalu sinkron manual).

### 5.3 Mode Terang/Gelap

Tombol di navigasi (ikon matahari/bulan) — pilihan disimpan di `localStorage`, per-browser, konsisten
di semua halaman dan kunjungan berikutnya.
