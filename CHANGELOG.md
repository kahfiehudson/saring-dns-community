# Changelog

Sumber data: `app/version.py` (`CHANGELOG`). Halaman `/changelog` di dashboard menampilkan isi yang sama.

## 1.0.0 - 2026-09-14 - Rilis Awal Community Edition

- Filtering DNS berbasis daftar resmi TrustPositif Komdigi (hanya dari endpoint resmi Komdigi, tanpa fallback ke sumber lain), lewat Unbound + modul Python (pymod) dan LMDB - metode blokir yang sama seperti Enterprise, hemat memori (~500MB RAM untuk ~9,5 juta domain) dan restart <1 detik.
- Dashboard: status update blocklist terakhir, kontrol Unbound (restart/reload manual), Query Real-time dan Server Real-time (CPU/RAM/Disk/Load average), live tiap ~3 detik.
- Pengaturan DNS: jadwal sinkronisasi blocklist (1/3/6/12/24 jam), mode blokir (NXDOMAIN/redirect ke halaman blokir), mode resolusi (rekursif atau forward ke upstream, dengan DNS-over-TLS opsional ke upstream itu), DNSSEC, QNAME minimisation, kontrol akses (CIDR allow/refuse), logging query & verbosity, dan SafeSearch Enforcement (Google/YouTube/Bing/DuckDuckGo).
- Login admin tunggal (tanpa multi-user/role) dengan halaman Akun Saya untuk ganti password sendiri.
- Log: halaman log ringkas (journalctl gabungan unbound + service dashboard/update) dan Activity Log (20 aksi terakhir lewat dashboard).
- Changelog: riwayat versi & fitur, dilihat lewat tautan versi di footer setiap halaman dashboard.
- Mode terang/gelap, tersimpan per-browser (localStorage).
- Logo dashboard bawaan SARING DNS (navigasi, tab peramban, halaman login) - tidak bisa diganti sendiri di edisi ini.
