# SARING DNS Community

**SARING** (Secure Access & Resolution Intelligence Network Gateway) DNS &mdash; DNS filtering berbasis daftar
resmi **TrustPositif Komdigi**, dijalankan lewat **Unbound**, plus dashboard web untuk memantau & mengatur
fitur-fitur inti DNS-nya. Ini adalah edisi **Community**: versi ringan dengan fitur-fitur inti saja, cocok
untuk deployment kecil-menengah yang tidak butuh fitur enterprise (multi-user & role, 2FA, notifikasi
Telegram, backup otomatis, statistik lanjutan, API monitoring eksternal, TPROXY, DNS terenkripsi untuk
client, dsb).

## Arsitektur

```
Komdigi (trustpositif.komdigi.go.id/assets/db/domains_isp)
        │  (systemd timer, harian)
        ▼
scripts/fetch_trustpositif.py ──► lmdb/blocklist (pymod) ──► Unbound
                                                              (pymod)
dashboard (FastAPI) ──► conf.d/10-features.conf ─────────────┘
             (DNSSEC, QNAME minimisation, access control, SafeSearch, dst.)
```

- **Sumber data**: hanya endpoint resmi Komdigi (`.../assets/db/domains_isp`),
  berisi satu domain per baris, berukuran ratusan MB. Di-*stream* langsung
  oleh script updater; kalau endpoint resminya tidak reachable dari IP
  server (umum terjadi, lihat docs/SETUP.md), update gagal dan dicatat di
  status - tidak ada fallback ke sumber lain.
- **Metode blokir — tetap: pymod + LMDB**, satu-satunya mekanisme (tidak
  bisa diubah dari Settings): modul Python custom untuk Unbound yang
  mengecek domain (dan semua subdomain-nya, lewat parent-suffix matching —
  tanpa perlu entry wildcard terpisah) ke database LMDB yang
  di-*memory-map*, bukan dimuat penuh ke RAM Unbound. Terukur **~500MB RAM**
  untuk ~9,5 juta domain dengan cakupan subdomain penuh, restart <1 detik —
  dibanding pendekatan [RPZ](https://unbound.docs.nlnetlabs.nl/en/latest/topics/rpz.html)
  standar Unbound, yang butuh ~5,9GB (tanpa wildcard) atau OOM di 16GB+
  (dengan wildcard, karena RPZ butuh 2 record/domain dan Unbound memuat
  seluruh zona ke memori) — sehingga RPZ tidak dipakai sama sekali di edisi
  Community ini.
- **Dashboard**: FastAPI + SQLite, menulis ulang config Unbound
  (`unbound-checkconf` divalidasi dulu sebelum reload/restart, jadi config
  yang salah tidak akan pernah diterapkan).

## Fitur dashboard

Ringkasan di bawah ini. Untuk penjelasan tiap fitur secara teknis (metode/cara kerja, dependensi
seperti butuh "Catat semua query DNS" atau tidak, dsb.), lihat **docs/FITUR.md**.

- Ringkasan status update terakhir & statistik query Unbound.
- Login admin tunggal, dengan halaman **Akun Saya** untuk ganti password sendiri.
- **SafeSearch Enforcement**: paksa mode aman Google/YouTube/Bing/DuckDuckGo lewat `local-zone: redirect` + CNAME
  ke endpoint resmi tiap provider (Unbound sendiri yang meneruskan resolusinya) &mdash; nonaktif per default,
  toggle per provider di Pengaturan DNS. Blocklist tetap menang kalau domainnya juga ada di daftar blokir.
- **Query Real-time & Server Real-time**: query/detik dan CPU/RAM/Disk/load average, live tiap ~3 detik.
- Pengaturan fitur Unbound dari web:
  - Jadwal sinkronisasi blocklist (1/3/6/12/24 jam).
  - Mode blokir: NXDOMAIN atau redirect ke halaman blokir.
  - Mode resolusi: rekursif langsung atau forward ke upstream (dengan
    DNS-over-TLS opsional ke upstream itu).
  - DNSSEC, QNAME minimisation.
  - Access control (CIDR allow/refuse).
  - Logging query & verbosity.
- Halaman log ringkas (journalctl gabungan `unbound` + service dashboard/update), plus Activity Log
  (20 aksi terakhir lewat dashboard: simpan pengaturan, tambah/hapus domain, restart/reload manual, dst.).
- **Kontrol Unbound**: status Aktif/Tidak Aktif live di dashboard, plus tombol Restart &amp; Reload manual.
- **Changelog**: riwayat versi & fitur, dilihat lewat tautan versi di footer setiap halaman dashboard. Lihat
  juga `CHANGELOG.md` (sumber datanya di `app/version.py`) &mdash; setiap fitur/revisi baru wajib menaikkan
  versi dan menambah satu entri di sana.
- **Mode terang/gelap**: tombol di navigasi (ikon matahari/bulan), pilihan disimpan di browser (localStorage)
  jadi konsisten di semua halaman dan kunjungan berikutnya.

## Instalasi

**OS**: Ubuntu Server 22.04/24.04 LTS atau Debian 12 Bookworm. Root access.

**Spesifikasi minimum**: 2 vCPU, 2GB RAM, 10GB disk bebas. **Direkomendasikan**: 2 vCPU, 4-8GB RAM,
20GB+ disk — pymod+LMDB sendiri terukur cukup nyaman di sini (lihat catatan RAM di atas). Detail lengkap
&amp; alasannya di docs/SETUP.md §1.

```bash
sudo ./install.sh
```

Script ini meng-install Unbound + binding Python-nya (termasuk bootstrap DNSSEC root trust anchor dan
membebaskan port 53 dari `systemd-resolved` kalau ada — dua hal yang biasanya perlu perbaikan manual
di instalasi bersih), membuat user layanan `trustpositif`, menyalin aplikasi ke
`/opt/trustpositif-dashboard`, membuat virtualenv, menyambungkan `unbound.conf`, memasang systemd
service/timer, lalu menjalankan pengambilan data TrustPositif pertama kali.

Lihat **docs/SETUP.md** untuk penjelasan langkah demi langkah, model
permission, dan troubleshooting.

## Update manual

```bash
sudo -u trustpositif /opt/trustpositif-dashboard/venv/bin/python \
  /opt/trustpositif-dashboard/scripts/fetch_trustpositif.py
```

Update otomatis berjalan tiap hari lewat `trustpositif-update.timer`
(`systemctl status trustpositif-update.timer`).

## Keterbatasan penting

- **HTTPS**: DNS filtering hanya mengendalikan resolusi nama, bukan koneksi
  TLS. Untuk situs blokir mode *redirect*, browser tetap akan menampilkan
  peringatan sertifikat pada situs HTTPS karena nama domain di sertifikat
  tidak cocok dengan halaman blokir — ini keterbatasan bawaan semua DNS-based
  filtering (termasuk implementasi Trust Positif/Nawala yang sebenarnya).
- **DoH/DoT klien**: jika perangkat klien memakai resolver DNS-over-HTTPS
  sendiri (mis. browser dengan DoH bawaan diarahkan ke resolver publik), lalu
  lintas DNS-nya melewati server ini sama sekali — filtering perlu dipaksa
  lewat kebijakan jaringan/klien di luar cakupan proyek ini.
- **Legalitas & tanggung jawab**: sesuaikan penerapan dengan kebutuhan
  kepatuhan Anda (mis. kewajiban ISP di Permenkominfo terkait). Proyek ini
  hanya alat teknis; Anda yang menentukan kebijakan pemblokirannya.

## Keamanan

Dashboard berjalan di port 8080 **tanpa TLS bawaan** dan bisa memicu
`unbound-checkconf` / reload service lewat sudoers terbatas. Jangan
ekspos langsung ke internet publik — batasi lewat firewall/VPN, atau
jalankan:

```bash
sudo ./scripts/setup_nginx.sh dns-admin.contoh.id admin@contoh.id
```

untuk otomatis memasang nginx + sertifikat TLS Let's Encrypt di depannya
(butuh domain yang sudah di-DNS-kan ke server ini). Detail & opsi lain di
docs/SETUP.md bagian Keamanan.
